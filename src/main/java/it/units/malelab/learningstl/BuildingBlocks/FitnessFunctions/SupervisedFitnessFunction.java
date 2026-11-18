/*
 * Provenance: BUSTLE-derived file, modified in STL Repair.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * Original BUSTLE authorship is retained; local changes are identified in git history.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.FitnessFunctions;

import it.units.malelab.learningstl.TreeNodes.AbstractTreeNode;
import it.units.malelab.learningstl.BuildingBlocks.SignalBuilders.SupervisedSignalBuilder;
import eu.quanticol.moonlight.signal.Signal;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.*;
import java.util.function.BiFunction;


public class SupervisedFitnessFunction extends AbstractFitnessFunction {

    private static final double LOCAL_CLASSIFICATION_EPS = 1.0E-9;
    private final BiFunction<double[], double[], Double> function = (x, y) -> (x[0] - y[0]) / (Math.abs(x[1] + y[1]));
    private final long numPositive;
    private final long numNegative;
    private final String datasetVersion;
    private final Map<Signal<Map<String, Double>>, Integer> sourceTraceIndexes = new IdentityHashMap<>();

    public SupervisedFitnessFunction(String path, boolean localSearch, Random random) throws IOException {
        this(path, localSearch, random, true);
    }

    public SupervisedFitnessFunction(
            String path,
            boolean localSearch,
            Random random,
        boolean normalizeValues
    ) throws IOException {
        super(localSearch, random);
        SupervisedSignalBuilder supervisedSignalBuilder = new SupervisedSignalBuilder();
        this.signalBuilder = supervisedSignalBuilder;
        List<Signal<Map<String, Double>>> signals = supervisedSignalBuilder.parseSignals(
                path,
                normalizeValues,
                false
        );
        this.labels = this.signalBuilder.readVectorFromFile(path + "/labels.csv");
        for (int i = 0; i < signals.size(); i++) {
            this.sourceTraceIndexes.put(signals.get(i), i);
        }
        this.splitSignals(signals, 0.8, random);
        this.numPositive = Arrays.stream(this.labels).filter(x -> x > 0).count();
        this.numNegative = Arrays.stream(this.labels).filter(x -> x < 0).count();
        this.datasetVersion = hashDataset(path);
    }

    @Override
    public Double apply(AbstractTreeNode monitor) {
        try {
            double[] positiveResult = this.computeRobustness(monitor, this.positiveTraining, this.numPositive, false);
            double[] negativeResult = this.computeRobustness(monitor, this.negativeTraining, this.numNegative, false);
            double value = -this.function.apply(positiveResult, negativeResult);
            return Double.isFinite(value) ? value : PENALTY_VALUE;
        } catch (RuntimeException ex) {
            return PENALTY_VALUE;
        }
    }

    public double[] computeRobustness(AbstractTreeNode monitor, List<Signal<Map<String, Double>>> data, long num, boolean isNegative) {
        double[] result = new double[3];
        if (num <= 0 || data.isEmpty()) {
            return result;
        }
        double robustness;
        for (Signal<Map<String, Double>> signal : data) {
            robustness = this.monitorSignal(signal, monitor, isNegative);
            result[0] += robustness;
            result[1] += robustness * robustness;
        }
        result[1] = this.standardDeviation(result[1], result[0], num);
        result[0] /= num;
        return result;
    }

    // STL Repair addition: replay metrics used to compare a repaired formula
    // against oracle labels. The original BUSTLE class did not expose them.
    public double getLocalAccuracy(AbstractTreeNode monitor) {
        LocalClassificationMetrics metrics = evaluateLocalClassification(monitor);
        return metrics.accuracy;
    }

    public double getLocalBalancedAccuracy(AbstractTreeNode monitor) {
        LocalClassificationMetrics metrics = evaluateLocalClassification(monitor);
        return metrics.balancedAccuracy;
    }

    public double getLocalMcc(AbstractTreeNode monitor) {
        LocalClassificationMetrics metrics = evaluateLocalClassification(monitor);
        return metrics.mcc;
    }

    public int getLocalMisclassifiedCount(AbstractTreeNode monitor) {
        LocalClassificationMetrics metrics = evaluateLocalClassification(monitor);
        return metrics.misclassified;
    }

    public ClassificationSnapshot getLocalClassificationSnapshot(AbstractTreeNode monitor) {
        List<Integer> predicted = new ArrayList<>();
        List<Integer> expected = new ArrayList<>();
        List<Double> robustness = new ArrayList<>();
        List<Integer> traceIndexes = new ArrayList<>();
        List<String> partitions = new ArrayList<>();
        appendSnapshot(monitor, this.positiveTraining, 1, "positive_training",
                predicted, expected, robustness, traceIndexes, partitions);
        appendSnapshot(monitor, this.positiveTest, 1, "positive_test",
                predicted, expected, robustness, traceIndexes, partitions);
        appendSnapshot(monitor, this.negativeTraining, -1, "negative_training",
                predicted, expected, robustness, traceIndexes, partitions);
        appendSnapshot(monitor, this.negativeTest, -1, "negative_test",
                predicted, expected, robustness, traceIndexes, partitions);
        return new ClassificationSnapshot(predicted, expected, robustness, traceIndexes, partitions);
    }

    /** Signals in exactly the same order as {@link ClassificationSnapshot}. */
    public List<Signal<Map<String, Double>>> getLocalSignalsInSnapshotOrder() {
        List<Signal<Map<String, Double>>> ordered = new ArrayList<>();
        ordered.addAll(this.positiveTraining);
        ordered.addAll(this.positiveTest);
        ordered.addAll(this.negativeTraining);
        ordered.addAll(this.negativeTest);
        return Collections.unmodifiableList(ordered);
    }

    public String getDatasetVersion() {
        return datasetVersion;
    }

    private void appendSnapshot(
            AbstractTreeNode monitor,
            List<Signal<Map<String, Double>>> signals,
            int expectedLabel,
            String partition,
            List<Integer> predicted,
            List<Integer> expected,
            List<Double> robustness,
            List<Integer> traceIndexes,
            List<String> partitions
    ) {
        for (Signal<Map<String, Double>> signal : signals) {
            double value = monitorSignal(signal, monitor, false);
            robustness.add(value);
            predicted.add(value >= -LOCAL_CLASSIFICATION_EPS ? 1 : -1);
            expected.add(expectedLabel);
            traceIndexes.add(sourceTraceIndexes.getOrDefault(signal, -1));
            partitions.add(partition);
        }
    }

    private static String hashDataset(String path) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            for (String name : List.of("data.csv", "labels.csv", "times.csv", "time.csv")) {
                Path file = Path.of(path, name);
                if (Files.exists(file)) {
                    digest.update(Files.readAllBytes(file));
                }
            }
            StringBuilder out = new StringBuilder();
            for (byte value : digest.digest()) {
                out.append(String.format(Locale.ROOT, "%02x", value));
            }
            return out.toString();
        } catch (IOException | NoSuchAlgorithmException exception) {
            return Integer.toHexString(path.hashCode());
        }
    }

    public static final class ClassificationSnapshot {
        public final int[] predicted;
        public final int[] expected;
        public final double[] robustness;
        public final int[] traceIndexes;
        public final String[] partitions;
        public final double accuracy;
        public final int misclassified;

        ClassificationSnapshot(
                List<Integer> predicted,
                List<Integer> expected,
                List<Double> robustness,
                List<Integer> traceIndexes,
                List<String> partitions
        ) {
            this.predicted = predicted.stream().mapToInt(Integer::intValue).toArray();
            this.expected = expected.stream().mapToInt(Integer::intValue).toArray();
            this.robustness = robustness.stream().mapToDouble(Double::doubleValue).toArray();
            this.traceIndexes = traceIndexes.stream().mapToInt(Integer::intValue).toArray();
            this.partitions = partitions.toArray(String[]::new);
            int correct = 0;
            for (int i = 0; i < this.predicted.length; i++) {
                if (this.predicted[i] == this.expected[i]) {
                    correct++;
                }
            }
            this.misclassified = this.predicted.length - correct;
            this.accuracy = this.predicted.length == 0
                    ? Double.NaN
                    : (double) correct / (double) this.predicted.length;
        }
    }

    // STL Repair addition: repair-local form of the BUSTLE separation score.
    public double getLocalBustleFitness(AbstractTreeNode monitor) {
        if (monitor.toString().contains("null")) {
            return PENALTY_VALUE;
        }
        try {
            LocalRobustnessSummary positive = summarizeLocalRobustness(monitor, this.positiveTraining, this.positiveTest);
            LocalRobustnessSummary negative = summarizeLocalRobustness(monitor, this.negativeTraining, this.negativeTest);
            double denominator = Math.abs(positive.std + negative.std);
            double separation = denominator > 0.0
                    ? (positive.mean - negative.mean) / denominator
                    : (positive.mean - negative.mean) / 1.0E-12;
            double value = -separation;
            return Double.isFinite(value) ? value : PENALTY_VALUE;
        } catch (RuntimeException ex) {
            return PENALTY_VALUE;
        }
    }

    @SafeVarargs
    private final LocalRobustnessSummary summarizeLocalRobustness(
            AbstractTreeNode monitor,
            List<Signal<Map<String, Double>>>... groups
    ) {
        double sum = 0.0;
        double sumSquared = 0.0;
        int count = 0;
        for (List<Signal<Map<String, Double>>> group : groups) {
            for (Signal<Map<String, Double>> signal : group) {
                double robustness = monitorSignal(signal, monitor, false);
                sum += robustness;
                sumSquared += robustness * robustness;
                count++;
            }
        }
        if (count == 0) {
            return new LocalRobustnessSummary(0.0, 0.0);
        }
        double mean = sum / count;
        if (count == 1) {
            return new LocalRobustnessSummary(mean, 0.0);
        }
        double variance = (sumSquared - count * mean * mean) / (count - 1);
        return new LocalRobustnessSummary(mean, Math.sqrt(Math.max(0.0, variance)));
    }

    private LocalClassificationMetrics evaluateLocalClassification(AbstractTreeNode monitor) {
        if (monitor.toString().contains("null")) {
            return new LocalClassificationMetrics(Double.NaN, Integer.MAX_VALUE);
        }
        try {
        int correct = 0;
        int total = 0;
        int positiveCorrect = 0;
        int positiveTotal = 0;
        int negativeCorrect = 0;
        int negativeTotal = 0;
        int tp = 0;
        int tn = 0;
        int fp = 0;
        int fn = 0;
        for (Signal<Map<String, Double>> signal : this.positiveTraining) {
            if (monitorSignal(signal, monitor, false) >= -LOCAL_CLASSIFICATION_EPS) {
                correct++;
                positiveCorrect++;
                tp++;
            } else {
                fn++;
            }
            total++;
            positiveTotal++;
        }
        for (Signal<Map<String, Double>> signal : this.positiveTest) {
            if (monitorSignal(signal, monitor, false) >= -LOCAL_CLASSIFICATION_EPS) {
                correct++;
                positiveCorrect++;
                tp++;
            } else {
                fn++;
            }
            total++;
            positiveTotal++;
        }
        for (Signal<Map<String, Double>> signal : this.negativeTraining) {
            if (monitorSignal(signal, monitor, false) < -LOCAL_CLASSIFICATION_EPS) {
                correct++;
                negativeCorrect++;
                tn++;
            } else {
                fp++;
            }
            total++;
            negativeTotal++;
        }
        for (Signal<Map<String, Double>> signal : this.negativeTest) {
            if (monitorSignal(signal, monitor, false) < -LOCAL_CLASSIFICATION_EPS) {
                correct++;
                negativeCorrect++;
                tn++;
            } else {
                fp++;
            }
            total++;
            negativeTotal++;
        }
        if (total == 0) {
            return new LocalClassificationMetrics(Double.NaN, Integer.MAX_VALUE);
        }
        double accuracy = (double) correct / (double) total;
        double positiveRate = positiveTotal == 0 ? Double.NaN : (double) positiveCorrect / (double) positiveTotal;
        double negativeRate = negativeTotal == 0 ? Double.NaN : (double) negativeCorrect / (double) negativeTotal;
        double balancedAccuracy;
        if (Double.isNaN(positiveRate)) {
            balancedAccuracy = negativeRate;
        } else if (Double.isNaN(negativeRate)) {
            balancedAccuracy = positiveRate;
        } else {
            balancedAccuracy = (positiveRate + negativeRate) / 2.0;
        }
        double denominator = Math.sqrt(
                (double) (tp + fp)
                        * (double) (tp + fn)
                        * (double) (tn + fp)
                        * (double) (tn + fn)
        );
        double mcc = denominator == 0.0
                ? (correct == total ? 1.0 : 0.0)
                : ((double) tp * (double) tn - (double) fp * (double) fn) / denominator;
        return new LocalClassificationMetrics(accuracy, balancedAccuracy, mcc, total - correct);
        } catch (RuntimeException ex) {
            return new LocalClassificationMetrics(Double.NaN, Integer.MAX_VALUE);
        }
    }

    // TODO: they use plain old variance
    private double standardDeviation(double partialSumSquared, double partialSum, long num) {
        double mean = partialSum / (num - 1);
        return Math.sqrt((partialSumSquared / (num - 1)) - (mean * mean));
    }

    private static class LocalClassificationMetrics {
        final double accuracy;
        final double balancedAccuracy;
        final double mcc;
        final int misclassified;

        LocalClassificationMetrics(double accuracy, int misclassified) {
            this(accuracy, accuracy, Double.NaN, misclassified);
        }

        LocalClassificationMetrics(double accuracy, double balancedAccuracy, double mcc, int misclassified) {
            this.accuracy = accuracy;
            this.balancedAccuracy = balancedAccuracy;
            this.mcc = mcc;
            this.misclassified = misclassified;
        }
    }

    private static class LocalRobustnessSummary {
        final double mean;
        final double std;

        LocalRobustnessSummary(double mean, double std) {
            this.mean = mean;
            this.std = std;
        }
    }

}
