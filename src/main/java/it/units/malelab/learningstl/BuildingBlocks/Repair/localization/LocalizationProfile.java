/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This file builds on BUSTLE-derived interfaces but is a repository-specific extension.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.Repair.localization;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class LocalizationProfile {

    public static final class ScoredEdit {
        public final RepairEdit edit;
        public final double evidenceScore;
        public final double museScore;
        public final double editScore;
        public final int correctedMismatches;
        public final int correctedFalseSatisfactions;
        public final int correctedFalseViolations;
        public final int brokenCorrectTraces;
        public final List<Integer> correctedTraceIndexes;
        public final Set<String> symptoms;

        public ScoredEdit(
                RepairEdit edit,
                double evidenceScore,
                double museScore,
                double editScore,
                int correctedMismatches,
                int correctedFalseSatisfactions,
                int correctedFalseViolations,
                int brokenCorrectTraces,
                List<Integer> correctedTraceIndexes,
                Set<String> symptoms
        ) {
            this.edit = edit;
            this.evidenceScore = evidenceScore;
            this.museScore = museScore;
            this.editScore = editScore;
            this.correctedMismatches = correctedMismatches;
            this.correctedFalseSatisfactions = correctedFalseSatisfactions;
            this.correctedFalseViolations = correctedFalseViolations;
            this.brokenCorrectTraces = brokenCorrectTraces;
            this.correctedTraceIndexes = Collections.unmodifiableList(new ArrayList<>(correctedTraceIndexes));
            this.symptoms = Collections.unmodifiableSet(new LinkedHashSet<>(symptoms));
        }
    }

    private final String formula;
    private final String datasetVersion;
    private final List<ScoredEdit> scoredEdits;
    private final Map<String, Double> evidenceScores;
    private final Map<String, Set<String>> nodeSymptoms;
    private final Map<String, Integer> ceClassSupport;

    public LocalizationProfile(
            String formula,
            String datasetVersion,
            List<ScoredEdit> scoredEdits,
            Map<String, Double> evidenceScores,
            Map<String, Set<String>> nodeSymptoms,
            Map<String, Integer> ceClassSupport
    ) {
        this.formula = formula;
        this.datasetVersion = datasetVersion;
        this.scoredEdits = new ArrayList<>(scoredEdits);
        this.scoredEdits.sort(Comparator.comparingDouble((ScoredEdit item) -> item.editScore).reversed());
        this.evidenceScores = Collections.unmodifiableMap(new LinkedHashMap<>(evidenceScores));
        Map<String, Set<String>> symptomCopy = new LinkedHashMap<>();
        nodeSymptoms.forEach((path, symptoms) -> symptomCopy.put(
                path, Collections.unmodifiableSet(new LinkedHashSet<>(symptoms))
        ));
        this.nodeSymptoms = Collections.unmodifiableMap(symptomCopy);
        this.ceClassSupport = Collections.unmodifiableMap(new LinkedHashMap<>(ceClassSupport));
    }

    public String getFormula() {
        return formula;
    }

    public String getDatasetVersion() {
        return datasetVersion;
    }

    public List<ScoredEdit> getScoredEdits() {
        return Collections.unmodifiableList(scoredEdits);
    }

    public double evidenceScore(String path) {
        return evidenceScores.getOrDefault(path, 0.0);
    }

    public Set<String> symptoms(String path) {
        return nodeSymptoms.getOrDefault(path, Set.of());
    }

    public int ceClassSupport(String path) {
        return ceClassSupport.getOrDefault(path, 0);
    }
}
