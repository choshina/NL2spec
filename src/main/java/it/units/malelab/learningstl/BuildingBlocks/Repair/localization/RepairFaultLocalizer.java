/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This file builds on BUSTLE-derived interfaces but is a repository-specific extension.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.Repair.localization;

import it.units.malelab.jgea.representation.tree.Tree;
import eu.quanticol.moonlight.signal.Signal;
import it.units.malelab.learningstl.BuildingBlocks.FitnessFunctions.SupervisedFitnessFunction;
import it.units.malelab.learningstl.BuildingBlocks.FitnessFunctions.SupervisedFitnessFunction.ClassificationSnapshot;
import it.units.malelab.learningstl.BuildingBlocks.Repair.RepairEditMutator;
import it.units.malelab.learningstl.BuildingBlocks.STLFormulaMapper;
import it.units.malelab.learningstl.TreeNodes.AbstractTreeNode;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.Set;

/** Separation-Guided Repair Localization using bounded one-step mutation probes. */
public final class RepairFaultLocalizer {

    private static final class TraceDiagnostic {
        final String formula;
        final int sourceIndex;
        final String traceId;
        final String groupId;
        final int predicted;
        final int expected;
        final MonitorEvidence monitor;
        final List<EvidenceItem> evidence;

        TraceDiagnostic(
                String formula,
                int sourceIndex,
                String traceId,
                String groupId,
                int predicted,
                int expected,
                MonitorEvidence monitor,
                List<EvidenceItem> evidence
        ) {
            this.formula = formula;
            this.sourceIndex = sourceIndex;
            this.traceId = traceId;
            this.groupId = groupId;
            this.predicted = predicted;
            this.expected = expected;
            this.monitor = monitor;
            this.evidence = evidence;
        }
    }

    public static final class GuidedMutation {
        public final Tree<String> genotype;
        public final RepairEdit edit;
        public final boolean guided;

        GuidedMutation(Tree<String> genotype, RepairEdit edit, boolean guided) {
            this.genotype = genotype;
            this.edit = edit;
            this.guided = guided;
        }
    }

    private static final class RewardStats {
        int count;
        double rewardSum;

        double ucb(int total, double beta) {
            double mean = count == 0 ? 0.0 : rewardSum / count;
            return mean + beta * Math.sqrt(2.0 * Math.log(total + 1.0) / (count + 1.0));
        }
    }

    private final RepairEditMutator mutator;
    private final SupervisedFitnessFunction fitness;
    private final GuidedMutationConfig config;
    private final Path reportPath;
    private final TraceMetadataIndex metadata;
    private final Map<String, LocalizationProfile> cache = new LinkedHashMap<>();
    private final Map<String, List<TraceDiagnostic>> diagnosticCache = new LinkedHashMap<>();
    private final Map<String, RewardStats> locationStats = new LinkedHashMap<>();
    private final Map<String, RewardStats> editStats = new LinkedHashMap<>();
    private int totalSelections;
    private int stagnationCount;
    private int gateExpansions;
    private double bestAccuracy = Double.NEGATIVE_INFINITY;
    private boolean firstGateWritten;
    /** Per localization: edits whose probe was evaluated, and edits not probed because of the type-priority rule. */
    private int editsTrialled;
    private int editsSkippedByType;

    public RepairFaultLocalizer(
            RepairEditMutator mutator,
            SupervisedFitnessFunction fitness,
            GuidedMutationConfig config,
            Path reportPath,
            Path traceMetadataPath
    ) {
        this.mutator = mutator;
        this.fitness = fitness;
        this.config = config;
        this.reportPath = reportPath;
        this.metadata = TraceMetadataIndex.load(traceMetadataPath);
    }

    public LocalizationProfile localize(Tree<String> parent) {
        int stagnationLevels = stagnationLevels();
        String cacheKey = parent.toString() + "|" + fitness.getDatasetVersion()
                + "|stagnationLevels=" + stagnationLevels;
        LocalizationProfile cached = cache.get(cacheKey);
        if (cached != null) {
            return cached;
        }

        AbstractTreeNode parentMonitor = new STLFormulaMapper().apply(parent);
        ClassificationSnapshot baseline = fitness.getLocalClassificationSnapshot(parentMonitor);
        List<Signal<Map<String, Double>>> signals = fitness.getLocalSignalsInSnapshotOrder();
        Map<String, Double> signalScales = signalScales(signals);
        Map<String, Map<String, Double>> groupNodeTotals = new LinkedHashMap<>();
        Map<String, Integer> groupSizes = new LinkedHashMap<>();
        Map<String, Set<String>> nodeSymptoms = new LinkedHashMap<>();
        // Largest self coefficient each node earned on any misclassified trace of this diagnosis (1 = no credit).
        Map<String, Double> nodeCredit = new LinkedHashMap<>();
        editsTrialled = 0;
        editsSkippedByType = 0;
        List<TraceDiagnostic> diagnostics = new ArrayList<>();
        int evidenceLimit = Math.min(baseline.predicted.length, signals.size());
        for (int i = 0; i < evidenceLimit; i++) {
            if (baseline.predicted[i] == baseline.expected[i]) {
                continue;
            }
            int sourceIndex = baseline.traceIndexes[i];
            String groupId = metadata.groupId(sourceIndex, baseline.predicted[i], baseline.expected[i]);
            MonitorEvidence monitor = MonitorEvidence.evaluate(
                    parentMonitor,
                    signals.get(i),
                    config.selfCoefficientCap,
                    signalScales
            );
            List<EvidenceItem> traceEvidence = monitor.backwardSlice(baseline.expected[i] > 0);
            Map<String, Double> traceNodeWeights = new LinkedHashMap<>();
            for (EvidenceItem item : traceEvidence) {
                traceNodeWeights.merge(item.nodePath, item.weight, Double::sum);
                nodeCredit.merge(item.nodePath, item.selfCoefficient, Math::max);
                Set<String> symptoms = nodeSymptoms.computeIfAbsent(
                        item.nodePath, ignored -> new LinkedHashSet<>()
                );
                for (String symptom : item.symptom.split(";")) {
                    if (!symptom.isBlank()) {
                        symptoms.add(symptom);
                    }
                }
            }
            Map<String, Double> groupTotals = groupNodeTotals.computeIfAbsent(
                    groupId, ignored -> new LinkedHashMap<>()
            );
            traceNodeWeights.forEach((path, weight) -> groupTotals.merge(path, weight, Double::sum));
            groupSizes.merge(groupId, 1, Integer::sum);
            diagnostics.add(new TraceDiagnostic(
                    parent.toString(), sourceIndex, metadata.get(sourceIndex).traceId,
                    groupId, baseline.predicted[i], baseline.expected[i], monitor, traceEvidence
            ));
        }
        Map<String, Double> nodeEvidence = new LinkedHashMap<>();
        Map<String, Integer> ceClassSupport = new LinkedHashMap<>();
        for (Map.Entry<String, Map<String, Double>> group : groupNodeTotals.entrySet()) {
            int groupSize = Math.max(1, groupSizes.getOrDefault(group.getKey(), 1));
            for (Map.Entry<String, Double> item : group.getValue().entrySet()) {
                double groupScore = Math.max(0.0, Math.min(1.0, item.getValue() / groupSize));
                nodeEvidence.merge(item.getKey(), groupScore,
                        (oldValue, newValue) -> 1.0 - (1.0 - oldValue) * (1.0 - newValue));
                if (groupScore > 0.0) {
                    ceClassSupport.merge(item.getKey(), 1, Integer::sum);
                }
            }
        }
        List<LocalizationProfile.ScoredEdit> scored = new ArrayList<>();
        int falseSat = countDirection(baseline, 1, -1);
        int falseVio = countDirection(baseline, -1, 1);
        List<RepairEdit> applicableEdits = mutator.enumerateApplicableEdits(parent);
        Set<String> editablePaths = new LinkedHashSet<>();
        for (RepairEdit edit : applicableEdits) {
            editablePaths.add(edit.getNodePath());
        }
        final Map<String, Double> evidenceForGate = nodeEvidence;
        // Cand: E_D > 0 and at least one legal edit; rank(v) = 1 + |{u in Cand : E_D(u) > E_D(v)}|,
        // so nodes tied on E_D share a rank and enter the gate together.
        List<String> candidates = evidenceForGate.entrySet().stream()
                .filter(item -> item.getValue() > 0.0 && editablePaths.contains(item.getKey()))
                .sorted(Map.Entry.<String, Double>comparingByValue().reversed())
                .map(Map.Entry::getKey)
                .toList();
        Map<String, Integer> ranks = new LinkedHashMap<>();
        for (String path : candidates) {
            double evidence = evidenceForGate.get(path);
            int greater = 0;
            for (String other : candidates) {
                if (evidenceForGate.get(other) > evidence + 1.0e-12 * Math.max(1.0, Math.abs(evidence))) {
                    greater++;
                }
            }
            ranks.put(path, 1 + greater);
        }
        // Gate widening has two triggers: stagnation of the generation-best accuracy
        // (expansionStep extra ranks per stagnationWindow generations, folded into the
        // initial k) and, inside one localization, the absence of any positive-MUSE edit
        // (k grows to the next rank that admits a new node).
        int k = topK(parent) + stagnationLevels;
        Set<String> gate = new LinkedHashSet<>();
        boolean widenedByMuse = false;
        boolean widenedByStagnation = stagnationLevels > 0;
        while (!candidates.isEmpty()) {
            Set<String> added = new LinkedHashSet<>();
            for (String path : candidates) {
                if (ranks.get(path) <= k && gate.add(path)) {
                    added.add(path);
                }
            }
            if (added.isEmpty() && k < candidates.size()) {
                k++;
                continue;
            }
            if (!added.isEmpty()) {
                if (gate.size() == added.size()) {
                    writeFirstGate(parent, k, gate);
                }
                List<LocalizationProfile.ScoredEdit> fresh = scoreEdits(
                        parent, baseline, falseSat, falseVio,
                        applicableEdits, added, nodeEvidence, nodeSymptoms, nodeCredit, true
                );
                scored.addAll(fresh);
                writeGateExpansion(k, gate.size(), candidates.size(), widenedByMuse,
                        widenedByStagnation && !widenedByMuse, fresh.size());
            }
            boolean positiveUtility = scored.stream().anyMatch(edit -> edit.museScore > 0.0);
            if (positiveUtility || gate.size() >= candidates.size()) {
                break;
            }
            widenedByMuse = true;
            k++;
        }

        LocalizationProfile profile = new LocalizationProfile(
                parent.toString(), fitness.getDatasetVersion(), scored,
                nodeEvidence, nodeSymptoms, ceClassSupport
        );
        cache.put(cacheKey, profile);
        diagnosticCache.put(cacheKey, diagnostics);
        writeEditCounts();
        printDiagnosis(baseline, profile);
        writeReport();
        writeEvidenceReports();
        return profile;
    }

    /** One line per localization: edits probed on D and edits not probed because of the type-priority rule. */
    private void writeEditCounts() {
        Path directory = reportPath.getParent() == null ? Path.of(".") : reportPath.getParent();
        String line = String.format(Locale.US, "{\"edits_trialled\":%d,\"edits_skipped_by_type\":%d}%n",
                editsTrialled, editsSkippedByType);
        try {
            Files.createDirectories(directory);
            Files.writeString(directory.resolve("edit_counts.jsonl"), line,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException exception) {
            System.err.println("could not write edit_counts.jsonl: " + exception.getMessage());
        }
    }

    /** The first gate this process builds, before any widening: what gate_hit is measured on. */
    private void writeFirstGate(Tree<String> parent, int k, Set<String> gate) {
        if (firstGateWritten) {
            return;
        }
        firstGateWritten = true;
        Path directory = reportPath.getParent() == null ? Path.of(".") : reportPath.getParent();
        String paths = gate.stream()
                .map(path -> "\"" + path.replace("\"", "\\\"") + "\"")
                .collect(java.util.stream.Collectors.joining(","));
        String json = String.format(Locale.US,
                "{\"k\":%d,\"size\":%d,\"formula_nodes\":%d,\"gate\":[%s]}%n",
                k, gate.size(), mutator.countFormulaNodes(parent), paths);
        try {
            Files.createDirectories(directory);
            Files.writeString(directory.resolve("first_gate.json"), json);
        } catch (IOException exception) {
            System.err.println("could not write first_gate.json: " + exception.getMessage());
        }
    }

    /** One line per gate step: k, |V_k|, |Cand|, why it widened, and how many edits were trialled. */
    private void writeGateExpansion(int k, int size, int candidates, boolean byMuse,
                                    boolean byStagnation, int editsTried) {
        Path directory = reportPath.getParent() == null ? Path.of(".") : reportPath.getParent();
        String line = String.format(Locale.US,
                "{\"k\":%d,\"gate_size\":%d,\"candidates\":%d,\"muse_expansion\":%b,"
                        + "\"stagnation_expansion\":%b,\"edits_tried\":%d}%n",
                k, size, candidates, byMuse, byStagnation, editsTried);
        try {
            Files.createDirectories(directory);
            Files.writeString(directory.resolve("gate_expansions.jsonl"), line,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException exception) {
            System.err.println("could not write gate_expansions.jsonl: " + exception.getMessage());
        }
    }

    /** k = max(initialTopK, ceil(rho * |V_phi|)) from the current hypothesis only. */
    private int topK(Tree<String> parent) {
        int floor = Math.max(1, config.initialTopK);
        if (config.topKRatio <= 0.0) {
            return floor;
        }
        return Math.max(floor, (int) Math.ceil(config.topKRatio * mutator.countFormulaNodes(parent) - 1.0e-9));
    }

    private static final class Probed {
        final LocalizationProfile.ScoredEdit scored;
        /** Mis(phi[e], D) is empty. */
        final boolean fitsD;

        Probed(LocalizationProfile.ScoredEdit scored, boolean fitsD) {
            this.scored = scored;
            this.fitsD = fitsD;
        }
    }

    /** Families tried first on a node that earned self credit: flip_temporal on G/F, flip_direction and toggle_not on atoms. */
    private static Set<String> priorityFamilies(double credit, String sourceNodeType) {
        if (credit <= 1.0 + 1.0e-9) {
            return Set.of();
        }
        if (sourceNodeType.equals("GLOBALLY") || sourceNodeType.equals("EVENTUALLY")) {
            return Set.of("flip_temporal");
        }
        if (sourceNodeType.equals("PROP")) {
            return Set.of("flip_direction", "toggle_not");
        }
        return Set.of();
    }

    private List<LocalizationProfile.ScoredEdit> scoreEdits(
            Tree<String> parent,
            ClassificationSnapshot baseline,
            int falseSat,
            int falseVio,
            List<RepairEdit> applicableEdits,
            Set<String> gate,
            Map<String, Double> nodeEvidence,
            Map<String, Set<String>> nodeSymptoms,
            Map<String, Double> nodeCredit,
            boolean requirePositiveEvidence
    ) {
        List<LocalizationProfile.ScoredEdit> scored = new ArrayList<>();
        // Type priority acts on gate nodes only; the no-gate and the non-SGRL localizations are left as they were.
        boolean prioritise = config.typePriority && config.localizationMethod.equals("sgrl");
        Map<RepairEdit, Probed> early = new java.util.IdentityHashMap<>();
        Set<String> fitNodes = new LinkedHashSet<>();
        if (prioritise) {
            Map<String, List<RepairEdit>> byNode = new LinkedHashMap<>();
            for (RepairEdit edit : applicableEdits) {
                if (gate.contains(edit.getNodePath())
                        && !(requirePositiveEvidence && nodeEvidence.getOrDefault(edit.getNodePath(), 0.0) <= 0.0)) {
                    byNode.computeIfAbsent(edit.getNodePath(), ignored -> new ArrayList<>()).add(edit);
                }
            }
            for (Map.Entry<String, List<RepairEdit>> node : byNode.entrySet()) {
                Set<String> families = priorityFamilies(
                        nodeCredit.getOrDefault(node.getKey(), 1.0), node.getValue().get(0).getSourceNodeType());
                boolean fits = false;
                for (RepairEdit edit : node.getValue()) {
                    if (families.contains(edit.getFamily())) {
                        Probed probed = probe(edit, parent, baseline, falseSat, falseVio, nodeEvidence, nodeSymptoms);
                        early.put(edit, probed);
                        fits |= probed != null && probed.fitsD;
                    }
                }
                if (fits) {
                    fitNodes.add(node.getKey());
                }
            }
        }
        for (RepairEdit edit : applicableEdits) {
            if (!gate.contains(edit.getNodePath())) {
                continue;
            }
            if (requirePositiveEvidence && nodeEvidence.getOrDefault(edit.getNodePath(), 0.0) <= 0.0) {
                continue;
            }
            Probed probed;
            if (fitNodes.contains(edit.getNodePath())) {
                // A first-batch edit fits D: only the fitting first-batch edits remain candidates.
                if (!early.containsKey(edit)) {
                    editsSkippedByType++;
                    continue;
                }
                probed = early.get(edit);
                if (probed == null || !probed.fitsD) {
                    continue;
                }
            } else {
                probed = early.containsKey(edit)
                        ? early.get(edit)
                        : probe(edit, parent, baseline, falseSat, falseVio, nodeEvidence, nodeSymptoms);
            }
            if (probed != null) {
                scored.add(probed.scored);
            }
        }
        return scored;
    }

    /** Applies the edit and scores it on D; null when the probe cannot be evaluated. */
    private Probed probe(
            RepairEdit edit,
            Tree<String> parent,
            ClassificationSnapshot baseline,
            int falseSat,
            int falseVio,
            Map<String, Double> nodeEvidence,
            Map<String, Set<String>> nodeSymptoms
    ) {
        double evidence = nodeEvidence.getOrDefault(edit.getNodePath(), 0.0);
        editsTrialled++;
        ClassificationSnapshot probe;
        try {
            probe = snapshot(edit.apply());
        } catch (RuntimeException exception) {
            return null;
        }
        if (!Double.isFinite(baseline.accuracy) || !Double.isFinite(probe.accuracy)) {
            return null;
        }
        int corrected = 0;
        int broken = 0;
        int correctedFalseSat = 0;
        int correctedFalseVio = 0;
        List<Integer> correctedTraceIndexes = new ArrayList<>();
        int limit = Math.min(baseline.predicted.length, probe.predicted.length);
        for (int i = 0; i < limit; i++) {
            boolean wasCorrect = baseline.predicted[i] == baseline.expected[i];
            boolean isCorrect = probe.predicted[i] == probe.expected[i];
            if (!wasCorrect && isCorrect) {
                corrected++;
                correctedTraceIndexes.add(baseline.traceIndexes[i]);
                if (baseline.predicted[i] == 1) {
                    correctedFalseSat++;
                } else {
                    correctedFalseVio++;
                }
            } else if (wasCorrect && !isCorrect) {
                broken++;
            }
        }
        double muse = muse(
                baseline.misclassified,
                falseSat,
                falseVio,
                correctedFalseSat,
                correctedFalseVio,
                broken
        );
        int sizeDelta = Math.max(0, treeSize(edit.apply()) - treeSize(parent));
        Set<String> symptoms = inferSymptoms(parent, edit, baseline, probe);
        symptoms.addAll(nodeSymptoms.getOrDefault(edit.getNodePath(), Set.of()));
        double score = config.evidenceWeight * evidence
                + config.museWeight * muse
                - config.sizeWeight * sizeDelta;
        LocalizationProfile.ScoredEdit result = new LocalizationProfile.ScoredEdit(
                edit, evidence, muse, score, corrected,
                correctedFalseSat, correctedFalseVio, broken,
                correctedTraceIndexes, symptoms
        );
        return new Probed(result, probe.misclassified == 0);
    }


    private void printDiagnosis(ClassificationSnapshot baseline, LocalizationProfile profile) {
        System.out.printf(
                "SGRL diagnosis: %d/%d misclassified traces, %d diagnostic edits, dataset=%s%n",
                baseline.misclassified,
                baseline.predicted.length,
                profile.getScoredEdits().size(),
                shortDatasetVersion(profile.getDatasetVersion())
        );
        for (int i = 0; i < baseline.predicted.length; i++) {
            if (baseline.predicted[i] == baseline.expected[i]) {
                continue;
            }
            System.out.printf(
                    Locale.US,
                    "  trace=%s partition=%s true_class=%s predicted_class=%s "
                            + "error=%s robustness=%.6f%n",
                    traceId(baseline.traceIndexes[i]),
                    baseline.partitions[i],
                    className(baseline.expected[i]),
                    className(baseline.predicted[i]),
                    baseline.predicted[i] > 0 ? "false_positive" : "false_negative",
                    baseline.robustness[i]
            );
        }
        System.out.println("  suspicious nodes and suggested mutations:");
        Set<String> printedNodes = new LinkedHashSet<>();
        int rank = 1;
        for (LocalizationProfile.ScoredEdit suggestion : profile.getScoredEdits()) {
            String nodePath = suggestion.edit.getNodePath();
            if (!printedNodes.add(nodePath)) {
                continue;
            }
            String symptoms = suggestion.symptoms.isEmpty()
                    ? "none"
                    : String.join(";", suggestion.symptoms);
            System.out.printf(
                    Locale.US,
                    "  #%d node=%s type=%s suggested=%s evidence=%.4f muse=%.4f ce_classes=%d score=%.4f "
                            + "corrected=%d corrected_traces=%s broken=%d symptoms=%s%n",
                    rank,
                    nodePath,
                    suggestion.edit.getSourceNodeType(),
                    suggestion.edit.getFamily(),
                    profile.evidenceScore(nodePath),
                    suggestion.museScore,
                    profile.ceClassSupport(nodePath),
                    suggestion.editScore,
                    suggestion.correctedMismatches,
                    traceIds(suggestion.correctedTraceIndexes),
                    suggestion.brokenCorrectTraces,
                    symptoms
            );
            if (rank++ >= config.initialTopK) {
                break;
            }
        }
        if (printedNodes.isEmpty()) {
            System.out.println("  no applicable diagnostic edit; using global exploration");
        }
    }

    private String traceId(int sourceIndex) {
        return sourceIndex < 0 ? "unknown" : metadata.get(sourceIndex).traceId;
    }

    private String traceIds(List<Integer> sourceIndexes) {
        if (sourceIndexes.isEmpty()) {
            return "none";
        }
        List<String> ids = new ArrayList<>();
        for (int sourceIndex : sourceIndexes) {
            ids.add(traceId(sourceIndex));
        }
        return String.join(";", ids);
    }

    private String className(int label) {
        return label > 0 ? "positive(+1)" : "negative(-1)";
    }

    private String shortDatasetVersion(String version) {
        if (version == null || version.length() <= 12) {
            return String.valueOf(version);
        }
        return version.substring(0, 12);
    }

    public GuidedMutation mutate(Tree<String> parent, Random random) {
        LocalizationProfile profile = localize(parent);
        List<LocalizationProfile.ScoredEdit> choices = profile.getScoredEdits();
        if (choices.isEmpty()) {
            List<Tree<String>> randomMutations = mutator.mutateMany(parent, random, 1, 1);
            Tree<String> genotype = randomMutations.isEmpty() ? Tree.copyOf(parent) : randomMutations.get(0);
            return new GuidedMutation(genotype, null, false);
        }
        // Algorithm 3 selects the globally best SE inside the final evidence gate.
        LocalizationProfile.ScoredEdit selected = choices.get(0);
        totalSelections++;
        return new GuidedMutation(selected.edit.apply(), selected.edit, true);
    }

    public double recordOutcome(
            Tree<String> parent,
            Tree<String> child,
            RepairEdit edit,
            double parentFitness,
            double childFitness
    ) {
        if (edit == null) {
            return 0.0;
        }
        ClassificationSnapshot before = snapshot(parent);
        ClassificationSnapshot after = snapshot(child);
        int broken = 0;
        int correctedFalseSat = 0;
        int correctedFalseVio = 0;
        int limit = Math.min(before.predicted.length, after.predicted.length);
        for (int i = 0; i < limit; i++) {
            boolean wasCorrect = before.predicted[i] == before.expected[i];
            boolean isCorrect = after.predicted[i] == after.expected[i];
            if (!wasCorrect && isCorrect) {
                if (before.predicted[i] == 1) {
                    correctedFalseSat++;
                } else {
                    correctedFalseVio++;
                }
            } else if (wasCorrect && !isCorrect) {
                broken++;
            }
        }
        int falseSat = countDirection(before, 1, -1);
        int falseVio = countDirection(before, -1, 1);
        double muse = muse(
                before.misclassified,
                falseSat,
                falseVio,
                correctedFalseSat,
                correctedFalseVio,
                broken
        );
        double fitnessGain = finite(parentFitness) && finite(childFitness)
                ? Math.max(-1.0, Math.min(1.0, parentFitness - childFitness))
                : 0.0;
        double clippedMuse = Math.max(-config.rewardClip, Math.min(config.rewardClip, muse));
        double reward = config.rewardWeight * clippedMuse
                + config.fitnessGainWeight * fitnessGain;
        update(locationStats, locationKey(parent.toString(), edit.getNodePath()), reward);
        update(editStats, editKey(parent.toString(), edit), reward);
        writeReport();
        return reward;
    }

    public double getFormulaUcbCoefficient() {
        return config.ucbBeta;
    }

    public void completeGeneration(double generationBestAccuracy) {
        if (Double.isFinite(generationBestAccuracy)
                && generationBestAccuracy > bestAccuracy + 1.0e-12) {
            bestAccuracy = generationBestAccuracy;
            stagnationCount = 0;
        } else {
            stagnationCount++;
            if (stagnationCount % config.stagnationWindow == 0) {
                gateExpansions++;
                System.out.printf(
                        "SGRL gate expansion: %d stagnant generations, +%d ranks%n",
                        stagnationCount, stagnationLevels()
                );
            }
        }
    }

    /** Extra node ranks granted by search stagnation; resets on improvement. */
    private int stagnationLevels() {
        return config.expansionStep * (stagnationCount / config.stagnationWindow);
    }

    public int getGateExpansions() {
        return gateExpansions;
    }

    public void writeReport() {
        try {
            if (reportPath.getParent() != null) {
                Files.createDirectories(reportPath.getParent());
            }
            List<String> lines = new ArrayList<>();
            lines.add("formula,self_coefficient_cap,node_path,node_type,evidence_score,ce_class_support,symptoms,best_operator,muse_score,best_edit_score,corrected_mismatches,corrected_false_satisfactions,corrected_false_violations,corrected_trace_ids,broken_correct_traces,location_selection_count,location_mean_reward,location_ucb,operator_selection_count,operator_mean_reward,operator_ucb,selection_count,mean_reward,rank");
            for (LocalizationProfile profile : cache.values()) {
                List<LocalizationProfile.ScoredEdit> rows = new ArrayList<>(profile.getScoredEdits());
                rows.sort(Comparator.comparingDouble((LocalizationProfile.ScoredEdit row) -> row.editScore).reversed());
                for (int i = 0; i < rows.size(); i++) {
                    LocalizationProfile.ScoredEdit row = rows.get(i);
                    RewardStats location = locationStats.get(locationKey(
                            profile.getFormula(), row.edit.getNodePath()
                    ));
                    RewardStats operator = editStats.get(editKey(profile.getFormula(), row.edit));
                    int locationCount = location == null ? 0 : location.count;
                    int operatorCount = operator == null ? 0 : operator.count;
                    double locationMean = location == null || location.count == 0
                            ? 0.0 : location.rewardSum / location.count;
                    double operatorMean = operator == null || operator.count == 0
                            ? 0.0 : operator.rewardSum / operator.count;
                    double locationUcb = location == null
                            ? config.ucbBeta : location.ucb(totalSelections, config.ucbBeta);
                    double operatorUcb = operator == null
                            ? config.ucbBeta : operator.ucb(totalSelections, config.ucbBeta);
                    lines.add(String.join(",",
                            csv(profile.getFormula()),
                            Integer.toString(config.selfCoefficientCap),
                            csv(row.edit.getNodePath()),
                            csv(row.edit.getSourceNodeType()),
                            number(profile.evidenceScore(row.edit.getNodePath())),
                            Integer.toString(profile.ceClassSupport(row.edit.getNodePath())),
                            csv(String.join(";", row.symptoms)),
                            csv(row.edit.getFamily()),
                            number(row.museScore),
                            number(row.editScore),
                            Integer.toString(row.correctedMismatches),
                            Integer.toString(row.correctedFalseSatisfactions),
                            Integer.toString(row.correctedFalseViolations),
                            csv(traceIds(row.correctedTraceIndexes)),
                            Integer.toString(row.brokenCorrectTraces),
                            Integer.toString(locationCount),
                            number(locationMean),
                            number(locationUcb),
                            Integer.toString(operatorCount),
                            number(operatorMean),
                            number(operatorUcb),
                            Integer.toString(operatorCount),
                            number(operatorMean),
                            Integer.toString(i + 1)
                    ));
                }
            }
            Files.write(reportPath, lines, StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
        } catch (IOException exception) {
            System.err.println("WARNING: cannot write SGRL report: " + exception.getMessage());
        }
    }

    private void writeEvidenceReports() {
        try {
            Path directory = reportPath.getParent() == null ? Path.of(".") : reportPath.getParent();
            Files.createDirectories(directory);
            List<String> robustnessRows = new ArrayList<>();
            List<String> cellRows = new ArrayList<>();
            List<String> evidenceRows = new ArrayList<>();
            robustnessRows.add("formula,trace_id,source_index,ce_group,predicted,expected,node_path,node_type,time,robustness,truth");
            cellRows.add("formula,trace_id,ce_group,node_path,node_type,start_time,end_time,truth,representative_time,representative_robustness");
            evidenceRows.add("formula,trace_id,source_index,ce_group,predicted,expected,node_path,node_type,time,site_responsibility,self_coefficient,raw_node_contribution,weight,target_truth,actual_truth,symptom");
            for (List<TraceDiagnostic> diagnostics : diagnosticCache.values()) {
                for (TraceDiagnostic diagnostic : diagnostics) {
                    double[] times = diagnostic.monitor.getTimes();
                    for (MonitorEvidence.NodeSeries node : diagnostic.monitor.getSeries().values()) {
                        for (int i = 0; i < times.length; i++) {
                            double robustness = node.robustness[i];
                            robustnessRows.add(String.join(",",
                                    csv(diagnostic.formula), csv(diagnostic.traceId),
                                    Integer.toString(diagnostic.sourceIndex), csv(diagnostic.groupId),
                                    Integer.toString(diagnostic.predicted), Integer.toString(diagnostic.expected),
                                    csv(node.path), csv(node.type), number(times[i]),
                                    Double.isFinite(robustness) ? number(robustness) : "",
                                    Double.isFinite(robustness) ? Boolean.toString(robustness >= 0.0) : ""
                            ));
                        }
                        for (TemporalCell cell : node.cells) {
                            cellRows.add(String.join(",",
                                    csv(diagnostic.formula), csv(diagnostic.traceId), csv(diagnostic.groupId),
                                    csv(node.path), csv(node.type), number(cell.startTime), number(cell.endTime),
                                    Boolean.toString(cell.truth), number(cell.representativeTime),
                                    number(cell.representativeRobustness)
                            ));
                        }
                    }
                    for (EvidenceItem item : diagnostic.evidence) {
                        evidenceRows.add(String.join(",",
                                csv(diagnostic.formula), csv(diagnostic.traceId),
                                Integer.toString(diagnostic.sourceIndex), csv(diagnostic.groupId),
                                Integer.toString(diagnostic.predicted), Integer.toString(diagnostic.expected),
                                csv(item.nodePath), csv(item.nodeType), number(item.time),
                                number(item.siteResponsibility), number(item.selfCoefficient),
                                number(item.rawNodeContribution), number(item.weight),
                                Boolean.toString(item.targetTruth), Boolean.toString(item.actualTruth),
                                csv(item.symptom)
                        ));
                    }
                }
            }
            Files.write(directory.resolve("robustness_table.csv"), robustnessRows,
                    StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
            Files.write(directory.resolve("temporal_cells.csv"), cellRows,
                    StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
            Files.write(directory.resolve("evidence_report.csv"), evidenceRows,
                    StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
        } catch (IOException exception) {
            System.err.println("WARNING: cannot write SGRL evidence reports: " + exception.getMessage());
        }
    }

    private ClassificationSnapshot snapshot(Tree<String> genotype) {
        AbstractTreeNode monitor = new STLFormulaMapper().apply(genotype);
        return fitness.getLocalClassificationSnapshot(monitor);
    }

    private Set<String> inferSymptoms(
            Tree<String> parent,
            RepairEdit edit,
            ClassificationSnapshot before,
            ClassificationSnapshot after
    ) {
        Set<String> symptoms = new LinkedHashSet<>();
        if (edit.getSourceNodeType().equals("EVENTUALLY")
                && countDirection(before, 1, -1) > 0) {
            symptoms.add("possible_F_to_G_conflict");
        } else if (edit.getSourceNodeType().equals("GLOBALLY")) {
            symptoms.add("invariance_constraint");
        } else if (edit.getSourceNodeType().equals("IMPLIES")) {
            symptoms.add("implication");
        } else if (edit.getSourceNodeType().equals("PROP")) {
            symptoms.add("predicate_verdict_mismatch");
            if (parent.toString().contains(".implies")
                    && edit.getNodePath().contains("/left")
                    && countDirection(before, 1, -1) > 0) {
                symptoms.add("vacuous_antecedent_false");
            }
        }
        if (after.misclassified < before.misclassified) {
            symptoms.add("diagnostic_probe_improves");
        }
        return symptoms;
    }

    private double muse(
            int misclassified,
            int falseSat,
            int falseVio,
            int correctedFalseSat,
            int correctedFalseVio,
            int broken
    ) {
        if (misclassified <= 0) {
            return -config.brokenLambda * broken;
        }
        double repair;
        if (falseSat > 0 && falseVio > 0) {
            repair = 0.5 * (
                    (double) correctedFalseSat / falseSat
                            + (double) correctedFalseVio / falseVio
            );
        } else {
            repair = (double) (correctedFalseSat + correctedFalseVio) / misclassified;
        }
        return repair - config.brokenLambda * ((double) broken / misclassified);
    }

    private int countDirection(ClassificationSnapshot snapshot, int predicted, int expected) {
        int count = 0;
        for (int i = 0; i < snapshot.predicted.length; i++) {
            if (snapshot.predicted[i] == predicted && snapshot.expected[i] == expected) {
                count++;
            }
        }
        return count;
    }

    private int treeSize(Tree<String> tree) {
        int size = 1;
        for (int i = 0; i < tree.nChildren(); i++) {
            size += treeSize(tree.child(i));
        }
        return size;
    }

    private Map<String, Double> signalScales(List<Signal<Map<String, Double>>> signals) {
        Map<String, Double> minima = new LinkedHashMap<>();
        Map<String, Double> maxima = new LinkedHashMap<>();
        for (Signal<Map<String, Double>> signal : signals) {
            for (Double time : signal.getTimeSet()) {
                Map<String, Double> sample = signal.valueAt(time);
                if (sample == null) {
                    continue;
                }
                for (Map.Entry<String, Double> entry : sample.entrySet()) {
                    double value = entry.getValue() == null ? Double.NaN : entry.getValue();
                    if (!Double.isFinite(value)) {
                        continue;
                    }
                    minima.merge(entry.getKey(), value, Math::min);
                    maxima.merge(entry.getKey(), value, Math::max);
                }
            }
        }
        Map<String, Double> scales = new LinkedHashMap<>();
        for (String variable : minima.keySet()) {
            double range = maxima.getOrDefault(variable, minima.get(variable)) - minima.get(variable);
            scales.put(variable, range > 0.0 && Double.isFinite(range) ? range : 1.0);
        }
        return scales;
    }

    private void update(Map<String, RewardStats> stats, String key, double reward) {
        RewardStats value = stats.computeIfAbsent(key, ignored -> new RewardStats());
        value.count++;
        value.rewardSum += reward;
    }

    private String locationKey(String formula, String path) {
        return formula + "|" + path;
    }

    private String editKey(String formula, RepairEdit edit) {
        return formula + "|" + edit.key();
    }

    private boolean finite(double value) {
        return Double.isFinite(value) && Math.abs(value) < 1.0e100;
    }

    private String number(double value) {
        return String.format(Locale.US, "%.10f", value);
    }

    private String csv(String value) {
        return "\"" + String.valueOf(value).replace("\"", "\"\"") + "\"";
    }
}
