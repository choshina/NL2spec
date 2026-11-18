/*
 * Provenance: BUSTLE-derived file, modified in STL Repair.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * Local changes add semantic STL repair and responsibility-guided localization.
 * Original BUSTLE authorship is retained; see PROVENANCE.md and THIRD_PARTY.md.
 */
package it.units.malelab.learningstl;

import it.units.malelab.jgea.core.evolver.Evolver;
import it.units.malelab.jgea.core.evolver.RandomSearch;
import it.units.malelab.jgea.core.evolver.StandardEvolver;
import it.units.malelab.jgea.core.Factory;
import it.units.malelab.jgea.core.listener.Event;
import it.units.malelab.learningstl.BuildingBlocks.FitnessFunctions.AbstractFitnessFunction;
import it.units.malelab.learningstl.BuildingBlocks.FitnessFunctions.SupervisedFitnessFunction;
import it.units.malelab.learningstl.BuildingBlocks.ProblemClass;
import it.units.malelab.learningstl.BuildingBlocks.SeededTreeFactory;
import it.units.malelab.learningstl.BuildingBlocks.Repair.RepairEditMutator;
import it.units.malelab.learningstl.BuildingBlocks.Repair.RepairFitnessMetrics;
import it.units.malelab.learningstl.BuildingBlocks.Repair.localization.GuidedMutationConfig;
import it.units.malelab.learningstl.BuildingBlocks.Repair.localization.RepairFaultLocalizer;
import it.units.malelab.learningstl.BuildingBlocks.STLFormulaTreeParser;
import it.units.malelab.learningstl.BuildingBlocks.STLFormulaMapper;
import it.units.malelab.learningstl.TreeNodes.AbstractTreeNode;
import eu.quanticol.moonlight.signal.Signal;
import it.units.malelab.jgea.Worker;
import it.units.malelab.jgea.core.Individual;
import it.units.malelab.jgea.core.evolver.StandardWithEnforcedDiversityEvolver;
import it.units.malelab.jgea.core.evolver.stopcondition.Iterations;
import it.units.malelab.jgea.core.listener.Listener;
import it.units.malelab.jgea.core.listener.PrintStreamListener;
import it.units.malelab.jgea.core.listener.collector.*;
import it.units.malelab.jgea.core.operator.GeneticOperator;
import it.units.malelab.jgea.core.order.PartialComparator;
import it.units.malelab.jgea.core.selector.Tournament;
import it.units.malelab.jgea.core.selector.Worst;
import it.units.malelab.jgea.core.util.Args;
import it.units.malelab.jgea.core.util.Misc;
import it.units.malelab.jgea.representation.grammar.cfggp.GrammarBasedSubtreeMutation;
import it.units.malelab.jgea.representation.grammar.cfggp.GrammarRampedHalfAndHalf;
import it.units.malelab.jgea.representation.tree.SameRootSubtreeCrossover;
import it.units.malelab.jgea.representation.tree.Tree;
import it.units.malelab.learningstl.roge.Proportional;

import java.io.FileOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.concurrent.ExecutionException;


public class Main extends Worker {

    private static int seed;
    private static PrintStream out;
    private static String grammarPath;
    private static String inputPath;
    private static String datasetName;
    private static String outputRoot;
    private static boolean isLocalSearch;
    private static boolean disableRepairUntil;
    private static String modality;
    private static String evolverName;
    private static AbstractFitnessFunction f;
    private static double repairMinComplexityRatio;
    private static final List<Double> eps = new ArrayList<>() {{ add(0.0); add(0.001); add(0.0025); add(0.005); add(0.01); add(0.02); add(0.03); add(0.04); add(0.05); add(0.1); add(0.15); add(0.2); add(0.25); add(0.3); add(0.35); add(0.4); add(0.45); add(0.5); add(0.55); add(0.6); add(0.65); add(0.7); add(0.75); add(0.8); add(0.85); add(0.9); add(0.95); add(1.0); }};

    public static void main(String[] args) throws IOException {
        String errorMessage = "notFound";
        String random = Args.a(args, "seed", errorMessage);
        if (random.equals(errorMessage)) {
            throw new IllegalArgumentException("Random Seed Not Valid");
        }
        seed = Integer.parseInt(random);
        String dataset = Args.a(args, "dataset", null);
        datasetName = dataset;
        inputPath = Args.a(args, "dataPath", String.join("/", "data", dataset));
        isLocalSearch = Boolean.parseBoolean(Args.a(args, "local", null));
        disableRepairUntil = Boolean.parseBoolean(Args.a(args, "disableRepairUntil", "false"));
        modality = Args.a(args, "mod", null);
        evolverName = Args.a(args, "evolver", "bustle");
        boolean isFormulaSeeded = Args.a(args, "formula", null) != null;
        String explicitGrammarPath = Args.a(args, "grammarPath", null);
        if (explicitGrammarPath != null) {
            grammarPath = explicitGrammarPath;
        } else {
            String grammarVariant = Args.a(args, "grammarVariant", null);
            grammarPath = "./grammars/grammar";
            if (grammarVariant != null && !grammarVariant.isBlank()) {
                grammarPath += "_" + grammarVariant;
            }
            if (isFormulaSeeded) {
                grammarPath += "_seeded";
            }
            if (isLocalSearch) {
                grammarPath += "_local_search";
            }
            grammarPath += ".bnf";
        }
        outputRoot = Args.a(args, "output", null);
        Path outputDir = Path.of(outputRoot);
        java.nio.file.Files.createDirectories(outputDir);
        String outputPath = outputDir.resolve(seed + "." + evolverName + ".csv").toString();
        out = new PrintStream(new FileOutputStream(outputPath, false), true);
        new Main(args);
    }

    public Main(String[] args) {
        super(args);
    }

    private static int countRepairDatasetLabels(String dataPath) {
        try {
            Path labelsPath = Path.of(dataPath, "labels.csv");
            if (!Files.exists(labelsPath)) {
                return 0;
            }
            int count = 0;
            for (String line : Files.readAllLines(labelsPath)) {
                String trimmed = line.trim();
                if (trimmed.isEmpty()) {
                    continue;
                }
                for (String token : trimmed.split(",")) {
                    if (!token.trim().isEmpty()) {
                        count++;
                    }
                }
            }
            return count;
        } catch (IOException exception) {
            return 0;
        }
    }

    @Override
    public void run() {
        try {
            evolution();
        } catch (IOException | ExecutionException | InterruptedException e) {
            e.printStackTrace();
        }
    }

    private void evolution() throws IOException, ExecutionException, InterruptedException {
        Random r = new Random(seed);
        String repairMode = Args.a(args, "repairMode", "bustle");
        if (!modality.equals("supervised")) {
            throw new IllegalArgumentException("Only supervised mode is supported by the generic repair framework: " + modality);
        }
        boolean normalizeMoonlightValues = Boolean.parseBoolean(
                Args.a(args, "moonlightNormalizeValues", "false")
        );
        f = new SupervisedFitnessFunction(
                inputPath,
                isLocalSearch,
                r,
                normalizeMoonlightValues
        );
        STLFormulaMapper m = new STLFormulaMapper();
        final ProblemClass p = new ProblemClass(grammarPath, f, m);
        int populationSize = Integer.parseInt(Args.a(args, "population", "500"));
        int offspringSize = Integer.parseInt(Args.a(args, "offspring", Integer.toString(populationSize)));
        int iterations = Integer.parseInt(Args.a(args, "iterations", "50"));
        int repairIterations = Integer.parseInt(Args.a(args, "repairIterations", Args.a(args, "iterations", "20")));
        int repairMaxEvaluatedCandidates = Integer.parseInt(Args.a(args, "repairMaxEvaluatedCandidates", Args.a(args, "repairTopK", "0")));
        int repairBeamWidth = Integer.parseInt(Args.a(args, "repairBeamWidth", "1"));
        int numericOnlyCandidates = Integer.parseInt(Args.a(args, "numericOnlyCandidates", "10"));
        int structureRepairCandidates = Integer.parseInt(Args.a(args, "structureRepairCandidates", "16"));
        int explorationCandidates = Integer.parseInt(Args.a(args, "explorationCandidates", "0"));
        int deterministicRepairCandidates = Integer.parseInt(Args.a(args, "deterministicRepairCandidates", "-1"));
        int randomConstraintRepairCandidates = Integer.parseInt(Args.a(args, "randomConstraintRepairCandidates", "-1"));
        int randomConstraintDepth = Integer.parseInt(Args.a(args, "randomConstraintDepth", "4"));
        int repairEditMaxBudget = Integer.parseInt(Args.a(args, "repairEditMaxBudget", Args.a(args, "repairEditBudget", "1")));
        boolean repairFuzzQueue = Boolean.parseBoolean(Args.a(args, "repairFuzzQueue", repairMode.equals("bustle") ? "true" : "false"));
        int repairFuzzQueueLimit = Integer.parseInt(Args.a(args, "repairFuzzQueueLimit", "0"));
        String repairParentSelection = Args.a(args, "repairParentSelection", "ucb");
        boolean repairStopOnPerfectFlag = Boolean.parseBoolean(Args.a(args, "repairStopOnPerfect", "false"));
        int repairMultiSolutionMinTraces = Integer.parseInt(Args.a(args, "repairMultiSolutionMinTraces", "-1"));
        int repairDatasetTraceCount = countRepairDatasetLabels(inputPath);
        boolean repairStopOnPerfect = repairStopOnPerfectFlag;
        if (repairMultiSolutionMinTraces >= 0) {
            repairStopOnPerfect = repairStopOnPerfectFlag && repairDatasetTraceCount < repairMultiSolutionMinTraces;
        }
        String repairClassFocusMode = Args.a(args, "repairClassFocusMode", "off");
        String repairClassFocusFile = Args.a(args, "repairClassFocusFile", "");
        List<String> repairClassFocusFormulas = loadRepairClassFocusFormulas(repairClassFocusFile);
        repairMinComplexityRatio = Double.parseDouble(Args.a(args, "repairMinComplexityRatio", "0.6"));
        if (Args.a(args, "repairPopulation", null) != null) {
            int repairPopulation = Integer.parseInt(Args.a(args, "repairPopulation", null));
            numericOnlyCandidates = repairPopulation;
            structureRepairCandidates = 0;
            explorationCandidates = 0;
        }
        if (repairMode.equals("baseline")) {
            numericOnlyCandidates = 0;
            structureRepairCandidates = 0;
            explorationCandidates = 0;
            deterministicRepairCandidates = 0;
            randomConstraintRepairCandidates = 0;
        } else if (repairMode.equals("bustle")) {
            numericOnlyCandidates = 0;
            structureRepairCandidates = 0;
            explorationCandidates = Integer.parseInt(Args.a(args, "repairPopulation", Integer.toString(populationSize)));
            deterministicRepairCandidates = 0;
            randomConstraintRepairCandidates = 0;
        } else {
            throw new IllegalArgumentException("Unknown repairMode: " + repairMode);
        }
        if (deterministicRepairCandidates < 0) {
            deterministicRepairCandidates = 0;
        }
        if (randomConstraintRepairCandidates < 0) {
            randomConstraintRepairCandidates = 0;
        }
        if (evolverName.equals("repair")) {
            repairSearch(
                    p,
                    r,
                    12,
                    repairIterations,
                    numericOnlyCandidates,
                    structureRepairCandidates,
                    explorationCandidates,
                    deterministicRepairCandidates,
                    randomConstraintRepairCandidates,
                    randomConstraintDepth,
                    repairEditMaxBudget,
                    repairFuzzQueue,
                    repairFuzzQueueLimit,
                    repairStopOnPerfect,
                    repairMaxEvaluatedCandidates,
                    repairBeamWidth,
                    repairClassFocusMode,
                    repairClassFocusFormulas,
                    repairParentSelection,
                    repairMode
            );
            return;
        }
        Map<GeneticOperator<Tree<String>>, Double> operators = new LinkedHashMap<>();
        GeneticOperator<Tree<String>> mutation = new GrammarBasedSubtreeMutation<>(12, p.getGrammar());
        GeneticOperator<Tree<String>> crossover = new SameRootSubtreeCrossover<>(12);
        operators.put(mutation, 0.2d);
        operators.put(crossover, 0.8d);
        Evolver<Tree<String>, AbstractTreeNode, Double> evolver;
        if (evolverName.equals("bustle")) {
            evolver = new StandardWithEnforcedDiversityEvolver<>(
                p.getSolutionMapper(),
                genotypeFactory(args, p, 12),
                PartialComparator.from(Double.class).comparing(Individual::getFitness),
                populationSize,
                operators,
                new Tournament(5),
                new Worst(),
                offspringSize,
                true,
                100
            );
        }
        else if (evolverName.equals("random")) {
            evolver = new RandomSearch<>(
                p.getSolutionMapper(),
                genotypeFactory(args, p, 12),
                PartialComparator.from(Double.class).comparing(Individual::getFitness)
            );
        }
        else {
            evolver = new StandardEvolver<>(
                p.getSolutionMapper(),
                genotypeFactory(args, p, 6),
                PartialComparator.from(Double.class).comparing(Individual::getFitness),
                populationSize,
                operators,
                new Proportional(),
                new Worst(),
                offspringSize,
                true
            );
        }
        Listener<Tree<String>, AbstractTreeNode, Double> printListener = new PrintStreamListener<>(out, false, 0,
                ";", ";",  new Basic(), new Population(), new Diversity(), new BestInfo("%5.3f"), (DataCollector<Tree<String>, AbstractTreeNode, Double>) this::saveData);
        Collection<AbstractTreeNode> solutions = evolver.solve(Misc.cached(p.getFitnessFunction(), 10), new Iterations(iterations),
                r, this.executorService, Listener.onExecutor(printListener, this.executorService));
    }

    // STL Repair modification: construct seeded formula populations in addition
    // to the original grammar-only BUSTLE initialization path.
    private Factory<Tree<String>> genotypeFactory(String[] args, ProblemClass p, int maxDepth) throws IOException {
        Factory<Tree<String>> randomFactory = new GrammarRampedHalfAndHalf<>(0, maxDepth, p.getGrammar());
        String formulaPath = Args.a(args, "formula", null);
        if (formulaPath == null || formulaPath.isBlank()) {
            return randomFactory;
        }
        if (formulaPath.equals("auto")) {
            throw new IllegalArgumentException("formula=auto is no longer supported; pass an explicit formula CSV path");
        }
        String formula = formulaFromPath(Path.of(formulaPath), args);
        Tree<String> seed = parseSeededFormula(formula);
        p.getSolutionMapper().apply(seed);
        String formulaInit = Args.a(args, "formulaInit", "copies");
        boolean copiesOnly = !formulaInit.equals("mixed");
        System.out.printf(
                "Using seeded initial population from %s (formulaInit=%s, local=%s)%n",
                formulaPath,
                copiesOnly ? "copies" : "mixed",
                isLocalSearch
        );
        return new SeededTreeFactory(List.of(seed), randomFactory, copiesOnly);
    }

    private Tree<String> parseSeededFormula(String formula) {
        return STLFormulaTreeParser.parse(formula, useLocalPlaceholdersForSeededFormula());
    }

    private boolean useLocalPlaceholdersForSeededFormula() {
        String evolver = Args.a(args, "evolver", "");
        String repairMode = Args.a(args, "repairMode", "");
        if (evolver.equals("repair") && (repairMode.equals("bustle") || repairMode.equals("baseline"))) {
            return false;
        }
        return isLocalSearch;
    }

    private String formulaFromPath(Path path, String[] args) throws IOException {
        if (!path.toString().toLowerCase(Locale.ROOT).endsWith(".csv")) {
            return String.join(" ",
                    java.nio.file.Files.readAllLines(path).stream()
                            .map(String::trim)
                            .filter(line -> !line.isEmpty() && !line.startsWith("#"))
                            .toList()
            );
        }
        List<List<String>> records = parseCsvRecords(java.nio.file.Files.readString(path));
        if (records.size() < 2) {
            throw new IllegalArgumentException("CSV formula file has no data rows: " + path);
        }
        List<String> header = records.get(0);
        Map<String, Integer> columns = new LinkedHashMap<>();
        for (int i = 0; i < header.size(); i++) {
            columns.put(header.get(i), i);
        }
        String requestedColumn = Args.a(args, "formulaColumn", null);
        String formulaColumn = requestedColumn == null ? firstPresentColumn(columns,
                "repair_seed_formula",
                "readable_formula",
                "formula",
                "serialized"
        ) : requestedColumn;
        if (formulaColumn == null || !columns.containsKey(formulaColumn)) {
            throw new IllegalArgumentException("Formula column not found in " + path + ": " + formulaColumn);
        }

        int selectedRow = selectedCsvFormulaRow(records, columns, args);
        List<String> row = records.get(selectedRow);
        int formulaIndex = columns.get(formulaColumn);
        if (formulaIndex >= row.size() || row.get(formulaIndex).isBlank()) {
            throw new IllegalArgumentException("Empty formula at CSV row " + selectedRow + ", column " + formulaColumn);
        }
        String formula = row.get(formulaIndex).trim();
        System.out.printf(
                "Read seeded STL from CSV %s row=%d column=%s seed=%d%n",
                path,
                selectedRow,
                formulaColumn,
                seed
        );
        return formula;
    }

    private int selectedCsvFormulaRow(List<List<String>> records, Map<String, Integer> columns, String[] args) {
        String rowArg = Args.a(args, "formulaRow", null);
        if (rowArg != null && !rowArg.isBlank()) {
            int row = Integer.parseInt(rowArg);
            if (row < 1 || row >= records.size()) {
                throw new IllegalArgumentException("formulaRow must be a 1-based CSV data row within 1.." + (records.size() - 1));
            }
            return row;
        }
        Integer seedColumn = columns.get("seed");
        if (seedColumn != null) {
            for (int i = 1; i < records.size(); i++) {
                List<String> row = records.get(i);
                if (seedColumn < row.size() && row.get(seedColumn).trim().equals(Integer.toString(seed))) {
                    return i;
                }
            }
        }
        return 1;
    }

    private String firstPresentColumn(Map<String, Integer> columns, String... candidates) {
        for (String candidate : candidates) {
            if (columns.containsKey(candidate)) {
                return candidate;
            }
        }
        return null;
    }

    private List<List<String>> parseCsvRecords(String text) {
        List<List<String>> records = new ArrayList<>();
        List<String> record = new ArrayList<>();
        StringBuilder current = new StringBuilder();
        boolean quoted = false;
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c == '"') {
                if (quoted && i + 1 < text.length() && text.charAt(i + 1) == '"') {
                    current.append('"');
                    i++;
                } else {
                    quoted = !quoted;
                }
            } else if (c == ',' && !quoted) {
                record.add(current.toString());
                current.setLength(0);
            } else if ((c == '\n' || c == '\r') && !quoted) {
                if (c == '\r' && i + 1 < text.length() && text.charAt(i + 1) == '\n') {
                    i++;
                }
                record.add(current.toString());
                current.setLength(0);
                if (!(record.size() == 1 && record.get(0).isEmpty())) {
                    records.add(record);
                }
                record = new ArrayList<>();
            } else {
                current.append(c);
            }
        }
        if (current.length() > 0 || !record.isEmpty()) {
            record.add(current.toString());
            records.add(record);
        }
        return records;
    }

    private List<String> parseCsvLine(String line) {
        List<String> values = new ArrayList<>();
        StringBuilder current = new StringBuilder();
        boolean quoted = false;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (c == '"') {
                if (quoted && i + 1 < line.length() && line.charAt(i + 1) == '"') {
                    current.append('"');
                    i++;
                } else {
                    quoted = !quoted;
                }
            } else if (c == ',' && !quoted) {
                values.add(current.toString());
                current.setLength(0);
            } else {
                current.append(c);
            }
        }
        values.add(current.toString());
        return values;
    }

    private List<String> loadRepairClassFocusFormulas(String fileName) {
        if (fileName == null || fileName.isBlank()) {
            return List.of();
        }
        Path path = Path.of(fileName);
        if (!Files.exists(path)) {
            return List.of();
        }
        try {
            List<String> lines = Files.readAllLines(path);
            if (lines.isEmpty()) {
                return List.of();
            }
            List<String> header = parseCsvLine(lines.get(0));
            int selectedIndex = header.indexOf("selected");
            int formulaIndex = header.indexOf("class_formula");
            if (selectedIndex < 0 || formulaIndex < 0) {
                return List.of();
            }
            List<String> formulas = new ArrayList<>();
            for (int i = 1; i < lines.size(); i++) {
                List<String> row = parseCsvLine(lines.get(i));
                if (row.size() <= Math.max(selectedIndex, formulaIndex)) {
                    continue;
                }
                if (Boolean.parseBoolean(row.get(selectedIndex)) && !row.get(formulaIndex).isBlank()) {
                    formulas.add(row.get(formulaIndex));
                }
            }
            return formulas;
        } catch (IOException exception) {
            return List.of();
        }
    }

    // STL Repair addition: deterministic, fuzz-queue, beam, and localized
    // semantic repair search. This method is not present in BUSTLE reference
    // commit e945ad648036a05d788fe72f92b1ce6a41ddd98b.
    private void repairSearch(
            ProblemClass p,
            Random random,
            int maxDepth,
            int repairIterations,
            int numericOnlyCandidates,
            int structureRepairCandidates,
            int explorationCandidates,
            int deterministicRepairCandidates,
            int randomConstraintRepairCandidates,
            int randomConstraintDepth,
            int repairEditMaxBudget,
            boolean repairFuzzQueue,
            int repairFuzzQueueLimit,
            boolean repairStopOnPerfect,
            int repairMaxEvaluatedCandidates,
            int repairBeamWidth,
            String repairClassFocusMode,
            List<String> repairClassFocusFormulas,
            String repairParentSelection,
            String repairMode
    ) throws IOException {
        long repairStartNanos = System.nanoTime();
        Factory<Tree<String>> initialFactory = genotypeFactory(args, p, maxDepth);
        Factory<Tree<String>> randomFormulaFactory = new GrammarRampedHalfAndHalf<>(
                0,
                Math.max(1, Math.min(maxDepth, randomConstraintDepth)),
                p.getGrammar()
        );
        GrammarBasedSubtreeMutation<String> randomMutation = new GrammarBasedSubtreeMutation<>(maxDepth, p.getGrammar());
        RepairEditMutator repairEditMutator = new RepairEditMutator(
                0,
                repairClassFocusMode,
                repairClassFocusFormulas
        );
        RepairFaultLocalizer repairFaultLocalizer = null;
        String repairLocalization = Args.a(args, "repairLocalization", "off");
        if (!repairLocalization.equalsIgnoreCase("off") && f instanceof SupervisedFitnessFunction) {
            GuidedMutationConfig localizationConfig = new GuidedMutationConfig(
                    repairLocalization,
                    Integer.parseInt(Args.a(args, "repairLocalizationTopK", "3")),
                    Integer.parseInt(Args.a(args, "repairLocalizationExpansionStep", "2")),
                    Integer.parseInt(Args.a(args, "repairLocalizationStagnationWindow", "10")),
                    Double.parseDouble(Args.a(args, "repairLocalizationUcbBeta", "0.35")),
                    Integer.parseInt(Args.a(args, "repairLocalizationSelfCoefficientCap",
                            Args.a(args, "repairLocalizationTemporalCellLimit", "3"))),
                    Double.parseDouble(Args.a(args, "repairLocalizationEvidenceWeight", "0.5")),
                    Double.parseDouble(Args.a(args, "repairLocalizationMuseWeight", "2.0")),
                    Double.parseDouble(Args.a(args, "repairLocalizationSizeWeight", "0.05")),
                    Double.parseDouble(Args.a(args, "repairLocalizationBrokenLambda", "1.0")),
                    Double.parseDouble(Args.a(args, "repairLocalizationRewardWeight", "2.0")),
                    Double.parseDouble(Args.a(args, "repairLocalizationRewardClip", "1.0")),
                    Double.parseDouble(Args.a(args, "repairLocalizationFitnessGainWeight", "1.0")),
                    Double.parseDouble(Args.a(args, "repairLocalizationTopKRatio", "0.0")),
                    Boolean.parseBoolean(Args.a(args, "repairLocalizationTypePriority", "true"))
            );
            String reportFile = Args.a(
                    args,
                    "repairLocalizationReport",
                    Path.of(outputRoot, "localization_report.csv").toString()
            );
            repairFaultLocalizer = new RepairFaultLocalizer(
                    repairEditMutator,
                    (SupervisedFitnessFunction) f,
                    localizationConfig,
                    Path.of(reportFile),
                    Args.a(args, "repairTraceMetadata", "").isBlank()
                            ? null
                            : Path.of(Args.a(args, "repairTraceMetadata", ""))
            );
            System.out.printf(
                    Locale.US,
                    "Repair localization: %s (topK=%d, topKRatio=%.3f, typePriority=%s, report=%s)%n",
                    repairLocalization,
                    localizationConfig.initialTopK,
                    localizationConfig.topKRatio,
                    localizationConfig.typePriority,
                    reportFile
            );
        }
        RepairCandidate current = evaluateRepairCandidate(
                initialFactory.build(1, random).get(0),
                "initial",
                0,
                0,
                p,
                null
        );
        current = asCurrentBaseline(current);
        RepairCandidate initial = current;
        List<RepairCandidate> beam = new ArrayList<>();
        beam.add(current);
        Deque<RepairCandidate> fuzzQueue = new ArrayDeque<>();
        Set<String> fuzzQueueSeen = new LinkedHashSet<>();
        RepairCandidate bestSeen = current;
        if (repairFuzzQueue) {
            enqueueRepairCandidate(fuzzQueue, fuzzQueueSeen, current, repairFuzzQueueLimit);
        }
        writeRepairHeader();
        if (repairMode.equals("bustle")) {
            bustlePopulationSearch(
                    p,
                    random,
                    repairEditMutator,
                    current,
                    Math.max(1, repairIterations),
                    Math.max(1, explorationCandidates),
                    repairParentSelection,
                    repairMode,
                    repairStartNanos,
                    repairStopOnPerfect,
                    repairFaultLocalizer
            );
            return;
        }
        writeRepairRow(0, current, 1, repairMode, elapsedSeconds(repairStartNanos));
        if (repairMode.equals("baseline")) {
            return;
        }

        for (int iteration = 1; iteration <= repairIterations; iteration++) {
            List<RepairCandidate> parents;
            RepairCandidate fuzzParent = null;
            if (repairFuzzQueue) {
                if (fuzzQueue.isEmpty()) {
                    enqueueRepairCandidate(fuzzQueue, fuzzQueueSeen, bestSeen, repairFuzzQueueLimit);
                }
                fuzzParent = fuzzQueue.removeFirst();
                parents = List.of(fuzzParent);
            } else {
                parents = beam.isEmpty() ? List.of(current) : beam;
            }
            List<PendingRepairCandidate> pending = new ArrayList<>();

            for (int i = 0; i < numericOnlyCandidates; i++) {
                RepairCandidate parent = parents.get(i % parents.size());
                pending.add(new PendingRepairCandidate(
                        Tree.copyOf(parent.genotype),
                        "numeric-only",
                        iteration,
                        i,
                        repairCandidatePriority("numeric-only", parent, random)
                ));
            }
            for (int i = 0; i < structureRepairCandidates; i++) {
                RepairCandidate parent = parents.get(i % parents.size());
                Tree<String> genotype = randomMutation.mutate(parent.genotype, random);
                pending.add(new PendingRepairCandidate(
                        genotype == null ? Tree.copyOf(parent.genotype) : genotype,
                        "structure+parameter",
                        iteration,
                        numericOnlyCandidates + i,
                        repairCandidatePriority("structure+parameter", parent, random)
                ));
            }
            if (repairMode.equals("bustle")) {
                int repairEditBudget = Math.max(1, Math.min(Math.max(1, repairEditMaxBudget), iteration));
                int added = 0;
                int parentIndex = 0;
                while (added < explorationCandidates && parentIndex < parents.size()) {
                    RepairCandidate parent = parents.get(parentIndex++);
                    List<Tree<String>> repairEdits = repairEditMutator.mutateMany(
                            parent.genotype,
                            random,
                            explorationCandidates - added,
                            repairEditBudget
                    );
                    for (Tree<String> genotype : repairEdits) {
                        pending.add(new PendingRepairCandidate(
                                genotype,
                                "repair-edit-budget-" + repairEditBudget,
                                iteration,
                                numericOnlyCandidates + structureRepairCandidates + added,
                                repairCandidatePriority("semantic-local", parent, random)
                        ));
                        added++;
                        if (added >= explorationCandidates) {
                            break;
                        }
                    }
                }
            } else {
                for (int i = 0; i < explorationCandidates; i++) {
                    RepairCandidate parent = parents.get(i % parents.size());
                    Tree<String> genotype;
                    genotype = randomMutation.mutate(parent.genotype, random);
                    if (genotype == null) {
                        genotype = Tree.copyOf(parent.genotype);
                    }
                    pending.add(new PendingRepairCandidate(
                            genotype,
                            "semantic-local",
                            iteration,
                            numericOnlyCandidates + structureRepairCandidates + i,
                            repairCandidatePriority("semantic-local", parent, random)
                    ));
                }
            }
            int deterministicIndex = numericOnlyCandidates + structureRepairCandidates + explorationCandidates;
            int deterministicAdded = 0;
            for (int i = 0; i < parents.size() && deterministicAdded < deterministicRepairCandidates; i++) {
                RepairCandidate parent = parents.get(i);
                for (Tree<String> genotype : deterministicRepairGenotypes(parent, deterministicRepairCandidates - deterministicAdded)) {
                    pending.add(new PendingRepairCandidate(
                            genotype,
                            "deterministic-structure",
                            iteration,
                            deterministicIndex + deterministicAdded,
                            repairCandidatePriority("deterministic-structure", parent, random)
                    ));
                    deterministicAdded++;
                    if (deterministicAdded >= deterministicRepairCandidates) {
                        break;
                    }
                }
            }
            int randomConstraintIndex = deterministicIndex + deterministicAdded;
            int randomConstraintAdded = 0;
            for (int i = 0; i < parents.size() && randomConstraintAdded < randomConstraintRepairCandidates; i++) {
                RepairCandidate parent = parents.get(i);
                for (Tree<String> genotype : randomConstraintRepairGenotypes(
                        parent,
                        p,
                        randomFormulaFactory,
                        random,
                        randomConstraintRepairCandidates - randomConstraintAdded
                )) {
                    pending.add(new PendingRepairCandidate(
                            genotype,
                            "random-constraint",
                            iteration,
                            randomConstraintIndex + randomConstraintAdded,
                            repairCandidatePriority("random-constraint", parent, random)
                    ));
                    randomConstraintAdded++;
                    if (randomConstraintAdded >= randomConstraintRepairCandidates) {
                        break;
                    }
                }
            }
            int pendingEvaluationLimit = Math.max(0, repairMaxEvaluatedCandidates - parents.size());
            if (repairMaxEvaluatedCandidates > 0 && pending.size() > pendingEvaluationLimit) {
                pending.sort((a, b) -> Double.compare(b.priority, a.priority));
                pending = new ArrayList<>(pending.subList(0, pendingEvaluationLimit));
            }

            List<RepairCandidate> candidates = new ArrayList<>();
            if (repairFuzzQueue) {
                candidates.add(asCurrentBaseline(fuzzParent));
            } else {
                for (RepairCandidate beamCandidate : beam) {
                    candidates.add(asCurrentBaseline(beamCandidate));
                }
            }
            for (PendingRepairCandidate candidate : pending) {
                candidates.add(evaluateRepairCandidate(
                        candidate.genotype,
                        candidate.type,
                        candidate.iteration,
                        candidate.candidateIndex,
                        p,
                        initial
                ));
            }

            RepairCandidate iterationBest = candidates.stream()
                    .min((a, b) -> compareRepairCandidates(a, b, repairMode))
                    .orElse(current);
            if (compareRepairCandidates(iterationBest, bestSeen, repairMode) < 0) {
                bestSeen = asCurrentBaseline(iterationBest);
            }
            current = repairFuzzQueue ? bestSeen : iterationBest;
            if (repairFuzzQueue) {
                enqueueRepairCandidates(fuzzQueue, fuzzQueueSeen, candidates, repairMode, repairFuzzQueueLimit);
                // Children enter before the tested parent is rotated to the
                // tail, so the next iteration explores a newly generated
                // candidate instead of immediately testing the same parent.
                fuzzQueue.addLast(asCurrentBaseline(fuzzParent));
                if (repairFuzzQueueLimit > 0) {
                    while (fuzzQueue.size() > repairFuzzQueueLimit) {
                        RepairCandidate removed = fuzzQueue.removeFirst();
                        if (removed != null && removed.solution != null) {
                            fuzzQueueSeen.remove(removed.solution.toString());
                        }
                    }
                }
            } else {
                beam = nextRepairBeam(candidates, repairBeamWidth, repairMode);
            }
            if (repairMode.equals("bustle")) {
                writeRepairPopulationRows(
                        iteration,
                        candidates,
                        repairMode,
                        elapsedSeconds(repairStartNanos)
                );
            } else {
                writeRepairRow(iteration, current, candidates.size(), repairMode, elapsedSeconds(repairStartNanos));
            }
        }
    }

    // STL Repair addition: population-based repair over a formula corpus,
    // reusing BUSTLE data structures but implementing repair-specific search.
    private void bustlePopulationSearch(
            ProblemClass p,
            Random random,
            RepairEditMutator mutator,
            RepairCandidate seed,
            int generationCount,
            int populationSize,
            String repairParentSelection,
            String repairMode,
            long startNanos,
            boolean repairStopOnPerfect,
            RepairFaultLocalizer localizer
    ) {
        // Every method runs the same corpus loop (Algorithm 4): no method gets an extra
        // deterministic one-step queue, so equal Q_max, time and trial budgets mean equal
        // search effort. Without a localizer each child is one random edit on any node.
        List<Tree<String>> anchorEditQueue = List.of();
        int anchorEditCursor = 0;
        Map<String, RepairCandidate> corpus = new LinkedHashMap<>();
        Map<String, Integer> parentSelectionCounts = new LinkedHashMap<>();
        Map<String, Double> parentRewardSums = new LinkedHashMap<>();
        int[] totalParentSelections = new int[]{0};
        addToRepairCorpus(corpus, seed);

        for (int generation = 0; generation < generationCount; generation++) {
            List<RepairCandidate> evaluatedThisGeneration = new ArrayList<>();
            RepairCandidate anchor = copyCandidateWithRole(seed, "anchor", generation, 0);
            evaluatedThisGeneration.add(anchor);
            addToRepairCorpus(corpus, anchor);
            if (repairStopOnPerfect && isPerfectRepairCandidate(anchor)) {
                if (localizer != null) {
                    localizer.writeReport();
                }
                writeRepairPopulationRows(
                        generation,
                        evaluatedThisGeneration,
                        repairMode,
                        elapsedSeconds(startNanos)
                );
                return;
            }

            int childIndex = 1;
            int mutableSlots = Math.max(0, populationSize - evaluatedThisGeneration.size());
            // Algorithm 4 selects every repair parent from the formula corpus, for every method.
            int anchorSlots = 0;
            int corpusSlots = mutableSlots - anchorSlots;

            for (int i = 0; i < anchorSlots && evaluatedThisGeneration.size() < populationSize; i++) {
                Tree<String> genotype = null;
                RepairFaultLocalizer.GuidedMutation guidedMutation = null;
                if (localizer != null) {
                    guidedMutation = localizer.mutate(seed.genotype, random);
                    genotype = guidedMutation.genotype;
                } else {
                    while (anchorEditCursor < anchorEditQueue.size() && genotype == null) {
                        genotype = Tree.copyOf(anchorEditQueue.get(anchorEditCursor++));
                    }
                }
                if (genotype == null || genotype.toString().equals(seed.genotype.toString())) {
                    List<Tree<String>> mutations = mutator.mutateMany(seed.genotype, random, 1, 1);
                    if (!mutations.isEmpty()) {
                        genotype = mutations.get(0);
                    }
                }
                if (genotype == null) {
                    RepairCandidate fallback = copyCandidateWithRole(
                            seed,
                            "anchor-copy",
                            generation,
                            childIndex++
                    );
                    evaluatedThisGeneration.add(fallback);
                    continue;
                }
                RepairCandidate child = evaluateRepairCandidate(
                        genotype,
                        guidedMutation != null && guidedMutation.guided
                                ? "sgrl-anchor-guided"
                                : (generation == 0 ? "anchor-one-step-initial" : "anchor-one-step"),
                        generation,
                        childIndex++,
                        p,
                        seed
                );
                if (localizer != null && guidedMutation != null) {
                    localizer.recordOutcome(
                            seed.genotype,
                            genotype,
                            guidedMutation.edit,
                            seed.fitness,
                            child.fitness
                    );
                }
                if (child.solution == null
                        || child.misclassified == Integer.MAX_VALUE
                        || !Double.isFinite(child.fitness)) {
                    RepairCandidate fallback = copyCandidateWithRole(
                            seed,
                            "anchor-copy",
                            generation,
                            evaluatedThisGeneration.size()
                    );
                    evaluatedThisGeneration.add(fallback);
                } else {
                    evaluatedThisGeneration.add(child);
                    addToRepairCorpus(corpus, child);
                    if (repairStopOnPerfect && isPerfectRepairCandidate(child)) {
                        if (localizer != null) {
                            localizer.writeReport();
                        }
                        writeRepairPopulationRows(
                                generation,
                                evaluatedThisGeneration,
                                repairMode,
                                elapsedSeconds(startNanos)
                        );
                        return;
                    }
                }
            }

            for (int i = 0; i < corpusSlots && evaluatedThisGeneration.size() < populationSize; i++) {
                RepairCandidate parent = selectCorpusParent(
                        new ArrayList<>(corpus.values()),
                        parentSelectionCounts,
                        parentRewardSums,
                        totalParentSelections,
                        localizer == null ? 0.35 : localizer.getFormulaUcbCoefficient()
                );
                if (parent == null) {
                    parent = seed;
                }
                RepairFaultLocalizer.GuidedMutation guidedMutation = localizer == null
                        ? null
                        : localizer.mutate(parent.genotype, random);
                List<Tree<String>> mutations = guidedMutation == null
                        ? mutator.mutateMany(parent.genotype, random, 1, 1)
                        : List.of(guidedMutation.genotype);
                if (mutations.isEmpty() || mutations.get(0).toString().equals(parent.genotype.toString())) {
                    RepairCandidate fallback = copyCandidateWithRole(
                            parent,
                            "corpus-parent-copy",
                            generation,
                            childIndex++
                    );
                    evaluatedThisGeneration.add(fallback);
                    continue;
                }
                RepairCandidate child = evaluateRepairCandidate(
                        mutations.get(0),
                        guidedMutation != null && guidedMutation.guided
                                ? "sgrl-corpus-guided"
                                : "corpus-one-step",
                        generation,
                        childIndex++,
                        p,
                        seed
                );
                if (localizer != null && guidedMutation != null) {
                    double reward = localizer.recordOutcome(
                            parent.genotype,
                            mutations.get(0),
                            guidedMutation.edit,
                            parent.fitness,
                            child.fitness
                    );
                    parentRewardSums.merge(repairCandidateKey(parent), reward, Double::sum);
                }
                if (child.solution == null
                        || child.misclassified == Integer.MAX_VALUE
                        || !Double.isFinite(child.fitness)) {
                    RepairCandidate fallback = copyCandidateWithRole(
                            parent,
                            "corpus-parent-copy",
                            generation,
                            evaluatedThisGeneration.size()
                    );
                    evaluatedThisGeneration.add(fallback);
                } else {
                    evaluatedThisGeneration.add(child);
                    addToRepairCorpus(corpus, child);
                    if (repairStopOnPerfect && isPerfectRepairCandidate(child)) {
                        if (localizer != null) {
                            localizer.writeReport();
                        }
                        writeRepairPopulationRows(
                                generation,
                                evaluatedThisGeneration,
                                repairMode,
                                elapsedSeconds(startNanos)
                        );
                        return;
                    }
                }
            }

            while (evaluatedThisGeneration.size() < populationSize) {
                evaluatedThisGeneration.add(copyCandidateWithRole(
                        seed,
                        "anchor-copy",
                        generation,
                        evaluatedThisGeneration.size()
                ));
            }
            if (localizer != null) {
                double generationBestAccuracy = evaluatedThisGeneration.stream()
                        .mapToDouble(candidate -> candidate.accuracy)
                        .filter(Double::isFinite)
                        .max()
                        .orElse(Double.NaN);
                localizer.completeGeneration(generationBestAccuracy);
                localizer.writeReport();
            }
            writeRepairPopulationRows(
                    generation,
                    evaluatedThisGeneration,
                    repairMode,
                    elapsedSeconds(startNanos)
            );

        }
    }

    private boolean isPerfectRepairCandidate(RepairCandidate candidate) {
        return candidate != null
                && candidate.solution != null
                && Double.isFinite(candidate.fitness)
                && !Double.isNaN(candidate.accuracy)
                && candidate.accuracy >= 0.999999;
    }

    private double anchorMutationRatio(int generation, int generationCount) {
        if (generationCount <= 1) {
            return 0.50;
        }
        double progress = (double) generation / (double) Math.max(1, generationCount - 1);
        if (progress < 1.0 / 3.0) {
            return 0.50;
        }
        if (progress < 2.0 / 3.0) {
            return 0.30;
        }
        return 0.20;
    }

    private boolean addToRepairCorpus(Map<String, RepairCandidate> corpus, RepairCandidate candidate) {
        if (candidate == null
                || candidate.solution == null
                || candidate.genotype == null
                || candidate.misclassified == Integer.MAX_VALUE
                || !Double.isFinite(candidate.fitness)
                || isInvalidFormula(candidate.solution.toString())) {
            return false;
        }
        corpus.putIfAbsent(candidate.solution.toString(), asCurrentBaseline(candidate));
        return true;
    }

    private RepairCandidate selectCorpusParent(
            List<RepairCandidate> corpus,
            Map<String, Integer> parentSelectionCounts,
            Map<String, Double> parentRewardSums,
            int[] totalParentSelections,
            double ucbCoefficient
    ) {
        if (corpus.isEmpty()) {
            return null;
        }
        RepairCandidate selected = null;
        double bestUcb = Double.NEGATIVE_INFINITY;
        for (RepairCandidate candidate : corpus) {
            if (candidate.misclassified <= 0) {
                continue;
            }
            double ucb = repairParentUcb(
                    candidate,
                    parentSelectionCounts,
                    parentRewardSums,
                    totalParentSelections[0],
                    ucbCoefficient
            );
            if (selected == null
                    || ucb > bestUcb + 1.0E-12
                    || (Math.abs(ucb - bestUcb) <= 1.0E-12
                    && repairCandidateKey(candidate).compareTo(repairCandidateKey(selected)) < 0)) {
                selected = candidate;
                bestUcb = ucb;
            }
        }
        String key = repairCandidateKey(selected);
        parentSelectionCounts.put(key, parentSelectionCounts.getOrDefault(key, 0) + 1);
        totalParentSelections[0] += 1;
        return selected;
    }

    private String repairCandidateKey(RepairCandidate candidate) {
        if (candidate != null && candidate.solution != null) {
            return candidate.solution.toString();
        }
        if (candidate != null && candidate.genotype != null) {
            return candidate.genotype.toString();
        }
        return "";
    }

    private double repairParentUcb(
            RepairCandidate candidate,
            Map<String, Integer> parentSelectionCounts,
            Map<String, Double> parentRewardSums,
            int totalParentSelections,
            double ucbCoefficient
    ) {
        String key = repairCandidateKey(candidate);
        double n = Math.max(0, parentSelectionCounts.getOrDefault(key, 0));
        double meanReward = n <= 0.0 ? 0.0 : parentRewardSums.getOrDefault(key, 0.0) / n;
        double exploration = ucbCoefficient * Math.sqrt(
                Math.log(1.0 + totalParentSelections) / (1.0 + n)
        );
        return meanReward + exploration;
    }

    private RepairCandidate tournamentSelect(
            List<RepairCandidate> population,
            int tournamentSize,
            Random random,
            String repairMode
    ) {
        RepairCandidate winner = null;
        int draws = Math.max(1, Math.min(tournamentSize, population.size()));
        for (int i = 0; i < draws; i++) {
            RepairCandidate candidate = population.get(random.nextInt(population.size()));
            if (winner == null || compareRepairCandidates(candidate, winner, repairMode) < 0) {
                winner = candidate;
            }
        }
        return winner == null ? population.get(0) : winner;
    }

    private String admissibleRepairFormula(
            Tree<String> genotype,
            ProblemClass p,
            RepairCandidate seed
    ) {
        try {
            AbstractTreeNode solution = p.getSolutionMapper().apply(Tree.copyOf(genotype));
            String formula = solution == null ? null : solution.toString();
            if (isInvalidFormula(formula) || exceedsTemporalSupport(solution, p)) {
                return null;
            }
            return formula;
        } catch (RuntimeException exception) {
            return null;
        }
    }

    private void writeRepairPopulationRows(
            int iteration,
            List<RepairCandidate> candidates,
            String repairMode,
            double runtimeSeconds
    ) {
        for (int populationIndex = 0; populationIndex < candidates.size(); populationIndex++) {
            RepairCandidate candidate = candidates.get(populationIndex);
            if (candidate == null
                    || candidate.solution == null
                    || candidate.genotype == null
                    || candidate.misclassified == Integer.MAX_VALUE
                    || !Double.isFinite(candidate.fitness)
                    || isInvalidFormula(candidate.solution.toString())) {
                continue;
            }
            writeRepairRow(
                    iteration,
                    candidate,
                    candidates.size(),
                    repairMode,
                    runtimeSeconds,
                    populationIndex
            );
        }
    }

    private void enqueueRepairCandidates(
            Deque<RepairCandidate> queue,
            Set<String> seen,
            List<RepairCandidate> candidates,
            String repairMode,
            int limit
    ) {
        List<RepairCandidate> ordered = new ArrayList<>(candidates);
        ordered.sort((a, b) -> compareRepairCandidates(a, b, repairMode));
        for (RepairCandidate candidate : ordered) {
            enqueueRepairCandidate(queue, seen, candidate, limit);
        }
    }

    private void enqueueRepairCandidate(
            Deque<RepairCandidate> queue,
            Set<String> seen,
            RepairCandidate candidate,
            int limit
    ) {
        if (candidate == null
                || candidate.solution == null
                || candidate.genotype == null
                || candidate.misclassified == Integer.MAX_VALUE
                || !Double.isFinite(candidate.fitness)
                || isInvalidFormula(candidate.solution.toString())) {
            return;
        }
        String formula = candidate.solution.toString();
        if (!seen.add(formula)) {
            return;
        }
        queue.addLast(asCurrentBaseline(candidate));
        if (limit > 0) {
            while (queue.size() > limit) {
                RepairCandidate removed = queue.removeFirst();
                if (removed != null && removed.solution != null) {
                    seen.remove(removed.solution.toString());
                }
            }
        }
    }

    private List<Tree<String>> deterministicRepairGenotypes(RepairCandidate parent, int limit) {
        if (limit <= 0) {
            return List.of();
        }
        List<Tree<String>> genotypes = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        String formula = parent.solution.toString();
        for (String variant : deterministicFormulaVariants(formula)) {
            if (!seen.add(variant) || variant.equals(formula)) {
                continue;
            }
            try {
                genotypes.add(parseSeededFormula(variant));
            } catch (Exception ignored) {
                // Some textual rewrites can fall outside the seeded grammar; skip them.
            }
            if (genotypes.size() >= limit) {
                break;
            }
        }
        return genotypes;
    }

    private List<String> deterministicFormulaVariants(String formula) {
        LinkedHashSet<String> variants = new LinkedHashSet<>();
        addTemporalOperatorVariants(formula, variants);
        addNotVariants(formula, variants);
        addUntilVariants(formula, variants);
        return new ArrayList<>(variants);
    }


    private List<Tree<String>> randomConstraintRepairGenotypes(
            RepairCandidate parent,
            ProblemClass p,
            Factory<Tree<String>> randomFormulaFactory,
            Random random,
            int limit
    ) {
        if (limit <= 0) {
            return List.of();
        }
        List<Tree<String>> genotypes = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        String formula = parent.solution.toString();
        List<String> intervals = intervalsInFormula(formula);
        int maxAttempts = Math.max(30, limit * 20);
        for (int attempt = 0; attempt < maxAttempts && genotypes.size() < limit; attempt++) {
            String randomFormula = randomFormula(p, randomFormulaFactory, random);
            if (randomFormula == null || randomFormula.equals(formula)) {
                continue;
            }
            for (String variant : randomConstraintVariants(formula, randomFormula, intervals)) {
                if (!seen.add(variant) || variant.equals(formula)) {
                    continue;
                }
                try {
                    genotypes.add(parseSeededFormula(variant));
                } catch (Exception ignored) {
                    // The sampled formula may still fall outside the seeded parser form; skip it.
                }
                if (genotypes.size() >= limit) {
                    break;
                }
            }
        }
        return genotypes;
    }

    private String randomFormula(
            ProblemClass p,
            Factory<Tree<String>> randomFormulaFactory,
            Random random
    ) {
        try {
            List<Tree<String>> samples = randomFormulaFactory.build(1, random);
            if (samples.isEmpty()) {
                return null;
            }
            AbstractTreeNode solution = p.getSolutionMapper().apply(Tree.copyOf(samples.get(0)));
            String formula = solution == null ? null : solution.toString();
            if (formula == null || formula.isBlank() || formula.contains("null")) {
                return null;
            }
            return formula;
        } catch (Exception ignored) {
            return null;
        }
    }

    private List<String> randomConstraintVariants(String formula, String randomFormula, List<String> intervals) {
        LinkedHashSet<String> variants = new LinkedHashSet<>();
        variants.add("AND[" + formula + "," + randomFormula + "]");
        variants.add("AND[" + formula + ",NOT[" + randomFormula + "]]");
        variants.add("AND[" + randomFormula + "," + formula + "]");
        if (!disableRepairUntil) {
            for (String interval : intervals) {
                variants.add("UNTIL I=" + interval + "[" + randomFormula + "," + formula + "]");
                variants.add("UNTIL I=" + interval + "[" + formula + "," + randomFormula + "]");
            }
        }
        return new ArrayList<>(variants);
    }

    private List<String> intervalsInFormula(String formula) {
        LinkedHashSet<String> intervals = new LinkedHashSet<>();
        java.util.regex.Matcher matcher = java.util.regex.Pattern
                .compile("I=\\[(\\s*-?\\d+(?:\\.\\d+)?\\s*,\\s*-?\\d+(?:\\.\\d+)?\\s*)\\]")
                .matcher(formula);
        while (matcher.find()) {
            intervals.add("[" + matcher.group(1).trim() + "]");
        }
        if (intervals.isEmpty()) {
            intervals.add("[0, 1]");
        }
        return new ArrayList<>(intervals);
    }

    private void addTemporalOperatorVariants(String formula, Set<String> variants) {
        addOperatorReplacementVariants(formula, variants, "EVENTUALLY", "GLOBALLY");
        addOperatorReplacementVariants(formula, variants, "GLOBALLY", "EVENTUALLY");
    }

    private void addOperatorReplacementVariants(String formula, Set<String> variants, String from, String to) {
        int index = formula.indexOf(from);
        while (index >= 0) {
            variants.add(formula.substring(0, index) + to + formula.substring(index + from.length()));
            index = formula.indexOf(from, index + from.length());
        }
    }

    private void addNotVariants(String formula, Set<String> variants) {
        if (formula.startsWith("NOT[")) {
            int close = matchingSquareBracket(formula, 3);
            if (close == formula.length() - 1) {
                variants.add(formula.substring(4, formula.length() - 1));
            }
        } else {
            variants.add("NOT[" + formula + "]");
        }
        int index = formula.indexOf("NOT[");
        while (index >= 0) {
            int close = matchingSquareBracket(formula, index + 3);
            if (close > index) {
                variants.add(formula.substring(0, index) + formula.substring(index + 4, close) + formula.substring(close + 1));
            }
            index = formula.indexOf("NOT[", index + 4);
        }
    }

    private void addUntilVariants(String formula, Set<String> variants) {
        int index = formula.indexOf("UNTIL I=");
        while (index >= 0) {
            int intervalOpen = formula.indexOf('[', index);
            int intervalClose = intervalOpen < 0 ? -1 : matchingSquareBracket(formula, intervalOpen);
            int argsOpen = intervalClose < 0 ? -1 : formula.indexOf('[', intervalClose + 1);
            int argsClose = argsOpen < 0 ? -1 : matchingSquareBracket(formula, argsOpen);
            if (intervalOpen >= 0 && intervalClose > intervalOpen && argsOpen >= 0 && argsClose > argsOpen) {
                String interval = formula.substring(intervalOpen, intervalClose + 1);
                List<String> args = splitTopLevelFormulaArgs(formula.substring(argsOpen + 1, argsClose));
                if (args.size() == 2) {
                    String left = args.get(0);
                    String right = args.get(1);
                    String prefix = formula.substring(0, index);
                    String suffix = formula.substring(argsClose + 1);
                    variants.add(prefix + "EVENTUALLY I=" + interval + "[" + right + "]" + suffix);
                    variants.add(prefix + "GLOBALLY I=" + interval + "[" + left + "]" + suffix);
                    variants.add(prefix + "AND[GLOBALLY I=" + interval + "[" + left + "],EVENTUALLY I=" + interval + "[" + right + "]]" + suffix);
                }
            }
            index = formula.indexOf("UNTIL I=", index + 1);
        }
    }

    private int matchingSquareBracket(String text, int openIndex) {
        if (openIndex < 0 || openIndex >= text.length() || text.charAt(openIndex) != '[') {
            return -1;
        }
        int depth = 0;
        for (int i = openIndex; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c == '[') {
                depth++;
            } else if (c == ']') {
                depth--;
                if (depth == 0) {
                    return i;
                }
            }
        }
        return -1;
    }

    private List<String> splitTopLevelFormulaArgs(String text) {
        List<String> args = new ArrayList<>();
        int depth = 0;
        int start = 0;
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c == '[') {
                depth++;
            } else if (c == ']') {
                depth--;
            } else if (c == ',' && depth == 0) {
                args.add(text.substring(start, i).trim());
                start = i + 1;
            }
        }
        args.add(text.substring(start).trim());
        return args;
    }

    private List<RepairCandidate> nextRepairBeam(
            List<RepairCandidate> candidates,
            int repairBeamWidth,
            String repairMode
    ) {
        int beamWidth = Math.max(1, repairBeamWidth);
        List<RepairCandidate> sorted = new ArrayList<>(candidates);
        sorted.sort((a, b) -> compareRepairCandidates(a, b, repairMode));
        List<RepairCandidate> next = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (RepairCandidate candidate : sorted) {
            String formula = candidate.solution.toString();
            if (seen.add(formula)) {
                next.add(asCurrentBaseline(candidate));
            }
            if (next.size() >= beamWidth) {
                break;
            }
        }
        return next;
    }

    private double repairCandidatePriority(String type, RepairCandidate current, Random random) {
        double jitter = random.nextDouble() * 1.0E-6;
        if (type.equals("numeric-only")) {
            return (current.hintDomainCount > 0 ? 3.0 : 1.0) + jitter;
        }
        if (type.equals("structure+parameter")) {
            return (current.structureHintCount > 0 ? 2.0 : 1.0) + jitter;
        }
        if (type.equals("deterministic-structure")) {
            return (current.structureHintCount > 0 ? 4.0 : 1.5) + jitter;
        }
        if (type.equals("random-constraint")) {
            return (current.structureHintCount > 0 ? 4.5 : 2.0) + jitter;
        }
        return jitter;
    }

    private double elapsedSeconds(long startNanos) {
        return (System.nanoTime() - startNanos) / 1_000_000_000.0;
    }

    private RepairCandidate evaluateRepairCandidate(
            Tree<String> genotype,
            String type,
            int iteration,
            int candidateIndex,
            ProblemClass p,
            RepairCandidate baseline
    ) {
        Tree<String> copiedGenotype = Tree.copyOf(genotype);
        AbstractTreeNode solution = p.getSolutionMapper().apply(Tree.copyOf(copiedGenotype));
        AbstractTreeNode fallbackSolution = p.getSolutionMapper().apply(Tree.copyOf(copiedGenotype));
        String formula = solution.toString();
        double fitness = Double.POSITIVE_INFINITY;
        double accuracy = Double.NaN;
        int misclassified = Integer.MAX_VALUE;
        int hintDomainCount = 0;
        int structureHintCount = 0;
        RepairFitnessMetrics numericRepairMetrics = null;
        try {
            fitness = p.getFitnessFunction().apply(solution);
            if (p.getFitnessFunction() instanceof SupervisedFitnessFunction) {
                SupervisedFitnessFunction supervisedFitness =
                        (SupervisedFitnessFunction) p.getFitnessFunction();
                fitness = supervisedFitness.getLocalBustleFitness(solution);
                accuracy = supervisedFitness.getLocalAccuracy(solution);
                misclassified = supervisedFitness.getLocalMisclassifiedCount(solution);
                if ((isInvalidFormula(solution.toString())
                        || !Double.isFinite(fitness)
                        || Double.isNaN(accuracy)
                        || misclassified == Integer.MAX_VALUE
                        || exceedsTemporalSupport(solution, p))
                        && fallbackSolution != null
                        && !isInvalidFormula(fallbackSolution.toString())
                        && !exceedsTemporalSupport(fallbackSolution, p)) {
                    double fallbackFitness = supervisedFitness.getLocalBustleFitness(fallbackSolution);
                    double fallbackAccuracy = supervisedFitness.getLocalAccuracy(fallbackSolution);
                    int fallbackMisclassified = supervisedFitness.getLocalMisclassifiedCount(fallbackSolution);
                    if (Double.isFinite(fallbackFitness)
                            && !Double.isNaN(fallbackAccuracy)
                            && fallbackMisclassified != Integer.MAX_VALUE) {
                        solution = fallbackSolution;
                        formula = solution.toString();
                        fitness = fallbackFitness;
                        accuracy = fallbackAccuracy;
                        misclassified = fallbackMisclassified;
                    }
                }
            }
        } catch (RuntimeException ex) {
            fitness = Double.POSITIVE_INFINITY;
            accuracy = Double.NaN;
            misclassified = Integer.MAX_VALUE;
            numericRepairMetrics = null;
            if (p.getFitnessFunction() instanceof SupervisedFitnessFunction
                    && fallbackSolution != null
                    && !isInvalidFormula(fallbackSolution.toString())
                    && !exceedsTemporalSupport(fallbackSolution, p)) {
                SupervisedFitnessFunction supervisedFitness =
                        (SupervisedFitnessFunction) p.getFitnessFunction();
                try {
                    double fallbackFitness = supervisedFitness.getLocalBustleFitness(fallbackSolution);
                    double fallbackAccuracy = supervisedFitness.getLocalAccuracy(fallbackSolution);
                    int fallbackMisclassified = supervisedFitness.getLocalMisclassifiedCount(fallbackSolution);
                    if (Double.isFinite(fallbackFitness)
                            && !Double.isNaN(fallbackAccuracy)
                            && fallbackMisclassified != Integer.MAX_VALUE) {
                        solution = fallbackSolution;
                        formula = solution.toString();
                        fitness = fallbackFitness;
                        accuracy = fallbackAccuracy;
                        misclassified = fallbackMisclassified;
                    }
                } catch (RuntimeException ignored) {
                    // Keep the invalid marker; the candidate will be filtered.
                }
            }
        }
        String outputType = type;
        if (isInvalidFormula(formula) || !Double.isFinite(fitness) || exceedsTemporalSupport(solution, p)) {
            fitness = Double.POSITIVE_INFINITY;
            accuracy = Double.NaN;
            misclassified = Integer.MAX_VALUE;
        } else if (baseline != null
                && repairMinComplexityRatio > 0.0
                && formulaComplexity(solution) < Math.ceil(formulaComplexity(baseline.solution) * repairMinComplexityRatio)) {
            fitness = Double.POSITIVE_INFINITY;
            accuracy = Double.NaN;
            misclassified = Integer.MAX_VALUE;
        }
        return new RepairCandidate(
                copiedGenotype,
                solution,
                fitness,
                outputType,
                iteration,
                candidateIndex,
                accuracy,
                misclassified,
                hintDomainCount,
                structureHintCount,
                numericRepairMetrics
        );
    }

    private static boolean isInvalidFormula(String formula) {
        return formula == null
                || formula.contains("NaN")
                || formula.contains("Infinity")
                || formula.contains("null");
    }

    private static int formulaComplexity(AbstractTreeNode node) {
        if (node == null) {
            return 0;
        }
        return 1 + formulaComplexity(node.getFirstChild()) + formulaComplexity(node.getSecondChild());
    }

    private static boolean exceedsTemporalSupport(AbstractTreeNode solution, ProblemClass p) {
        double[] bounds = p.getFitnessFunction().getSignalBuilder().getTemporalBounds();
        if (bounds == null || bounds.length < 2) {
            return false;
        }
        double span = bounds[1] - bounds[0];
        return solution.getNecessaryLength() > span + 1.0E-9;
    }

    private RepairCandidate asCurrentBaseline(RepairCandidate candidate) {
        return new RepairCandidate(
                candidate.genotype,
                candidate.solution,
                candidate.fitness,
                candidate.type,
                candidate.iteration,
                candidate.candidateIndex,
                candidate.accuracy,
                candidate.misclassified,
                candidate.hintDomainCount,
                candidate.structureHintCount,
                new RepairFitnessMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)
        );
    }

    private RepairCandidate copyCandidateWithRole(
            RepairCandidate candidate,
            String type,
            int iteration,
            int candidateIndex
    ) {
        return new RepairCandidate(
                Tree.copyOf(candidate.genotype),
                candidate.solution,
                candidate.fitness,
                type,
                iteration,
                candidateIndex,
                candidate.accuracy,
                candidate.misclassified,
                candidate.hintDomainCount,
                candidate.structureHintCount,
                candidate.numericRepairMetrics
        );
    }

    private int compareRepairCandidates(RepairCandidate a, RepairCandidate b, String repairMode) {
        if (repairMode.equals("bustle")) {
            return compareBustleRepairCandidates(a, b);
        }
        return compareRepairCandidates(a, b);
    }

    private int compareBustleRepairCandidates(RepairCandidate a, RepairCandidate b) {
        int byAccuracy = Double.compare(
                Double.isNaN(b.accuracy) ? Double.NEGATIVE_INFINITY : b.accuracy,
                Double.isNaN(a.accuracy) ? Double.NEGATIVE_INFINITY : a.accuracy
        );
        if (byAccuracy != 0) {
            return byAccuracy;
        }
        int byMisclassified = Integer.compare(a.misclassified, b.misclassified);
        if (byMisclassified != 0) {
            return byMisclassified;
        }
        int byNewError = Double.compare(a.newError(), b.newError());
        if (byNewError != 0) {
            return byNewError;
        }
        int byComplexity = Integer.compare(a.complexity(), b.complexity());
        if (byComplexity != 0) {
            return byComplexity;
        }
        return Double.compare(a.fitness, b.fitness);
    }

    private int compareRepairCandidates(RepairCandidate a, RepairCandidate b) {
        int byFitness = Double.compare(a.fitness, b.fitness);
        if (byFitness != 0) {
            return byFitness;
        }
        int byMisclassified = Integer.compare(a.misclassified, b.misclassified);
        if (byMisclassified != 0) {
            return byMisclassified;
        }
        int byNewError = Double.compare(a.newError(), b.newError());
        if (byNewError != 0) {
            return byNewError;
        }
        return Double.compare(Double.isNaN(b.accuracy) ? Double.NEGATIVE_INFINITY : b.accuracy,
                Double.isNaN(a.accuracy) ? Double.NEGATIVE_INFINITY : a.accuracy);
    }

    private void writeRepairHeader() {
        out.println(String.join(";",
                "iteration",
                "mode",
                "candidateType",
                "candidateIndex",
                "candidateCount",
                "fitness",
                "accuracy",
                "newError",
                "editDistance",
                "formulaComplexity",
                "runtimeSeconds",
                "misclassified",
                "hintDomains",
                "structureHints",
                "numericRepairFitness",
                "numericDeltaSError",
                "numericDeltaSAll",
                "numericNewError",
                "serialized"
        ));
    }

    private void writeRepairRow(int iteration, RepairCandidate candidate, int candidateCount, String repairMode, double runtimeSeconds) {
        writeRepairRow(
                iteration,
                candidate,
                candidateCount,
                repairMode,
                runtimeSeconds,
                candidate.candidateIndex
        );
    }

    private void writeRepairRow(
            int iteration,
            RepairCandidate candidate,
            int candidateCount,
            String repairMode,
            double runtimeSeconds,
            int outputCandidateIndex
    ) {
        String formula = candidate.solution.toString();
        out.println(String.join(";",
                Integer.toString(iteration),
                csv(repairMode),
                csv(candidate.type),
                Integer.toString(outputCandidateIndex),
                Integer.toString(candidateCount),
                String.format(Locale.US, "%.8f", candidate.fitness),
                Double.isNaN(candidate.accuracy) ? "" : String.format(Locale.US, "%.8f", candidate.accuracy),
                candidate.numericRepairMetrics == null ? "" : String.format(Locale.US, "%.8f", candidate.numericRepairMetrics.getNewError()),
                candidate.numericRepairMetrics == null ? "" : String.format(Locale.US, "%.8f", candidate.numericRepairMetrics.getEditCost()),
                Integer.toString(candidate.complexity()),
                String.format(Locale.US, "%.8f", runtimeSeconds),
                candidate.misclassified == Integer.MAX_VALUE ? "" : Integer.toString(candidate.misclassified),
                Integer.toString(candidate.hintDomainCount),
                Integer.toString(candidate.structureHintCount),
                candidate.numericRepairMetrics == null ? "" : String.format(Locale.US, "%.8f", candidate.numericRepairMetrics.getRepairFitness()),
                candidate.numericRepairMetrics == null ? "" : String.format(Locale.US, "%.8f", candidate.numericRepairMetrics.getDeltaSError()),
                candidate.numericRepairMetrics == null ? "" : String.format(Locale.US, "%.8f", candidate.numericRepairMetrics.getDeltaSAll()),
                candidate.numericRepairMetrics == null ? "" : String.format(Locale.US, "%.8f", candidate.numericRepairMetrics.getNewError()),
                csv(formula)
        ));
    }

    private String csv(String value) {
        if (value == null) {
            return "";
        }
        return "\"" + value.replace("\"", "\"\"") + "\"";
    }

    private static class RepairCandidate {
        final Tree<String> genotype;
        final AbstractTreeNode solution;
        final double fitness;
        final String type;
        final int iteration;
        final int candidateIndex;
        final double accuracy;
        final int misclassified;
        final int hintDomainCount;
        final int structureHintCount;
        final RepairFitnessMetrics numericRepairMetrics;

        RepairCandidate(
                Tree<String> genotype,
                AbstractTreeNode solution,
                double fitness,
                String type,
                int iteration,
                int candidateIndex,
                double accuracy,
                int misclassified,
                int hintDomainCount,
                int structureHintCount,
                RepairFitnessMetrics numericRepairMetrics
        ) {
            this.genotype = genotype;
            this.solution = solution;
            this.fitness = fitness;
            this.type = type;
            this.iteration = iteration;
            this.candidateIndex = candidateIndex;
            this.accuracy = accuracy;
            this.misclassified = misclassified;
            this.hintDomainCount = hintDomainCount;
            this.structureHintCount = structureHintCount;
            this.numericRepairMetrics = numericRepairMetrics;
        }

        double newError() {
            return numericRepairMetrics == null ? 0.0 : numericRepairMetrics.getNewError();
        }

        double editDistance() {
            return numericRepairMetrics == null ? 0.0 : numericRepairMetrics.getEditCost();
        }

        int complexity() {
            return complexity(solution);
        }

        private static int complexity(AbstractTreeNode node) {
            if (node == null) {
                return 0;
            }
            return 1 + complexity(node.getFirstChild()) + complexity(node.getSecondChild());
        }
    }

    private static class PendingRepairCandidate {
        final Tree<String> genotype;
        final String type;
        final int iteration;
        final int candidateIndex;
        final double priority;

        PendingRepairCandidate(
                Tree<String> genotype,
                String type,
                int iteration,
                int candidateIndex,
                double priority
        ) {
            this.genotype = genotype;
            this.type = type;
            this.iteration = iteration;
            this.candidateIndex = candidateIndex;
            this.priority = priority;
        }
    }

    public static double[] postProcess(AbstractTreeNode bestFormula, AbstractFitnessFunction f, double epsilon) {
        double result;
        double count = 0.0;
        double robustness = 0.0;
        double[] out = new double[4];
        for (Signal<Map<String, Double>> signal : f.getPositiveTest()) {
            result = f.monitorSignal(signal, bestFormula, false);
            if (result < - epsilon) {
                ++count;
            }
            robustness += result;
        }
        out[0] = count / f.getPositiveTest().size();
        out[1] = robustness / f.getPositiveTest().size();
        count = 0.0;
        for (Signal<Map<String, Double>> signal : f.getNegativeTest()) {
            result = f.monitorSignal(signal, bestFormula, false);
            if (result >= - epsilon) {
                ++count;
            }
            robustness += f.monitorSignal(signal, bestFormula, false);
        }
        out[2] = count / f.getNegativeTest().size();
        out[3] = robustness / f.getNegativeTest().size();
        return out;
    }

    private List<Item> saveData(Event<? extends Tree<String>, ? extends AbstractTreeNode, ? extends Double> event) {
        AbstractTreeNode best = event.getOrderedPopulation().firsts().iterator().next().getSolution();
        List<Item> out = new ArrayList<>();
        out.add(new Item("local", (isLocalSearch) ? 1 : 0, "%1d"));
        for (double epsilon : eps) {
            double[] perf = postProcess(best, f, epsilon);
            out.add(new Item(epsilon + ".positive.miss", perf[0], "%1.2"));
            out.add(new Item(epsilon + ".negative.miss", perf[2], "%1.2"));
            out.add(new Item(epsilon + ".positive.rob", perf[1], "%4.8f"));
            out.add(new Item(epsilon + ".negative.rob", perf[3], "%4.8f"));
            epsilon = - epsilon;
            perf = postProcess(best, f, epsilon);
            out.add(new Item(epsilon + ".positive.miss", perf[0], "%1.2"));
            out.add(new Item(epsilon + ".negative.miss", perf[2], "%1.2"));
            out.add(new Item(epsilon + ".positive.rob", perf[1], "%4.8f"));
            out.add(new Item(epsilon + ".negative.rob", perf[3], "%4.8f"));
        }
        out.add(new Item("serialized", best.toString(), "%s"));
        return out;
    }

}
