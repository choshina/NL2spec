/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This repository-specific component builds on the BUSTLE-derived backend.
 * See PROVENANCE.md and the repository-level THIRD_PARTY.md.
 */
package it.units.malelab.learningstl;

import eu.quanticol.moonlight.signal.Signal;
import it.units.malelab.jgea.representation.tree.Tree;
import it.units.malelab.learningstl.BuildingBlocks.STLFormulaMapper;
import it.units.malelab.learningstl.BuildingBlocks.STLFormulaTreeParser;
import it.units.malelab.learningstl.BuildingBlocks.SignalBuilders.SupervisedSignalBuilder;
import it.units.malelab.learningstl.TreeNodes.AbstractTreeNode;

import java.io.IOException;
import java.io.PrintStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

public class EvaluateFormulaOnDataset {

    public static void main(String[] args) throws IOException {
        Map<String, String> parsedArgs = parseArgs(args);
        String dataset = parsedArgs.getOrDefault("dataset", "linear");
        String dataPath = parsedArgs.getOrDefault("dataPath", "data/" + dataset);
        String formula = parsedArgs.get("formula");
        String batchFile = parsedArgs.getOrDefault("batchFile", "");
        if (batchFile.isBlank() && (formula == null || formula.isBlank())) {
            throw new IllegalArgumentException("Pass formula=<STL formula> or batchFile=<file>");
        }
        double epsilon = Double.parseDouble(parsedArgs.getOrDefault("epsilon", "0.0"));
        String output = parsedArgs.getOrDefault("output", "");
        boolean strictLengthCheck = Boolean.parseBoolean(parsedArgs.getOrDefault("strictLengthCheck", "true"));
        boolean normalizeValues = Boolean.parseBoolean(parsedArgs.getOrDefault("normalizeValues", "true"));
        boolean renumberTimes = Boolean.parseBoolean(parsedArgs.getOrDefault("renumberTimes", "true"));
        String shortSignalPolicy = parsedArgs.getOrDefault("shortSignalPolicy", "penalty");

        SupervisedSignalBuilder signalBuilder = new SupervisedSignalBuilder();
        List<Signal<Map<String, Double>>> signals = signalBuilder.parseSignals(dataPath, normalizeValues, renumberTimes);
        Path labelsPath = Path.of(dataPath, "labels.csv");
        double[] labels = Files.exists(labelsPath)
                ? signalBuilder.readVectorFromFile(labelsPath.toString())
                : new double[0];

        if (batchFile.isBlank()) {
            evaluateOne(formula, output, signals, labels, epsilon, strictLengthCheck, shortSignalPolicy);
            return;
        }
        // Batch mode: the dataset is read once; every line is "<output csv>\t<formula>". A formula that
        // fails (unparsable, unmappable) leaves no output file, exactly like a failed single call, and
        // the batch goes on.
        for (String line : Files.readAllLines(Path.of(batchFile))) {
            if (line.isBlank()) {
                continue;
            }
            int tab = line.indexOf('\t');
            String batchOutput = line.substring(0, tab);
            String batchFormula = line.substring(tab + 1);
            try {
                evaluateOne(batchFormula, batchOutput, signals, labels, epsilon, strictLengthCheck, shortSignalPolicy);
            } catch (Exception exception) {
                Files.deleteIfExists(Path.of(batchOutput));
                System.out.println("failed=" + batchOutput + "\t" + exception.getClass().getSimpleName());
            }
        }
    }

    private static void evaluateOne(
            String formula,
            String output,
            List<Signal<Map<String, Double>>> signals,
            double[] labels,
            double epsilon,
            boolean strictLengthCheck,
            String shortSignalPolicy
    ) throws IOException {
        Tree<String> tree = STLFormulaTreeParser.parse(formula, false);
        AbstractTreeNode monitor = new STLFormulaMapper().apply(tree);

        PrintStream out = output.isBlank() ? System.out : new PrintStream(output);
        out.println("trace,label,signalSize,necessaryLength,robustness,status,correct");
        int sat = 0;
        int vio = 0;
        int correct = 0;
        int incorrect = 0;
        int skipped = 0;
        for (int i = 0; i < signals.size(); i++) {
            Signal<Map<String, Double>> signal = signals.get(i);
            double robustness;
            String status;
            if (isTooShort(signal, monitor.getNecessaryLength())) {
                if (shortSignalPolicy.equals("penalty")) {
                    robustness = -1.0;
                    status = "VIO";
                    vio++;
                } else if (strictLengthCheck || shortSignalPolicy.equals("skip")) {
                    robustness = Double.NaN;
                    status = "TOO_SHORT";
                    skipped++;
                } else {
                    try {
                        robustness = monitor.getOperator().apply(signal).monitor(signal).valueAt(signal.start());
                        status = robustness >= -epsilon ? "SAT" : "VIO";
                        if (status.equals("SAT")) {
                            sat++;
                        } else {
                            vio++;
                        }
                    } catch (Exception exception) {
                        robustness = Double.NaN;
                        status = "ERROR:" + exception.getClass().getSimpleName();
                        skipped++;
                    }
                }
            } else {
                try {
                    robustness = monitor.getOperator().apply(signal).monitor(signal).valueAt(signal.start());
                    status = robustness >= -epsilon ? "SAT" : "VIO";
                    if (status.equals("SAT")) {
                        sat++;
                    } else {
                        vio++;
                    }
                } catch (Exception exception) {
                    robustness = Double.NaN;
                    status = "ERROR:" + exception.getClass().getSimpleName();
                    skipped++;
                }
            }

            String labelText = labels.length > i ? format(labels[i]) : "";
            String correctText = "";
            if (!Double.isNaN(robustness) && labels.length > i) {
                boolean expectedSat = labels[i] >= 0.0;
                boolean actualSat = status.equals("SAT");
                boolean isCorrect = expectedSat == actualSat;
                correctText = Boolean.toString(isCorrect);
                if (isCorrect) {
                    correct++;
                } else {
                    incorrect++;
                }
            }
            out.println(String.join(",",
                    Integer.toString(i),
                    labelText,
                    Integer.toString(signal.size()),
                    Integer.toString(monitor.getNecessaryLength()),
                    format(robustness),
                    status,
                    correctText
            ));
        }
        out.printf(
                Locale.US,
                "# summary,total=%d,sat=%d,vio=%d,skipped=%d,correct=%d,incorrect=%d,accuracy=%s%n",
                signals.size(),
                sat,
                vio,
                skipped,
                correct,
                incorrect,
                (correct + incorrect) == 0 ? "" : format((double) correct / (double) (correct + incorrect))
        );
        if (!output.isBlank()) {
            out.close();
            System.out.println("saved=" + output);
        }
    }

    private static boolean isTooShort(Signal<Map<String, Double>> signal, int necessaryLength) {
        return signal.isEmpty() || signal.end() - signal.start() < necessaryLength;
    }

    private static Map<String, String> parseArgs(String[] args) {
        Map<String, String> parsed = new LinkedHashMap<>();
        for (String arg : args) {
            int split = arg.indexOf('=');
            if (split < 0) {
                throw new IllegalArgumentException("Expected key=value argument, got: " + arg);
            }
            parsed.put(arg.substring(0, split), arg.substring(split + 1));
        }
        return parsed;
    }

    private static String format(double value) {
        if (Double.isNaN(value)) {
            return "NaN";
        }
        if (Math.rint(value) == value) {
            return String.format(Locale.US, "%.0f", value);
        }
        return String.format(Locale.US, "%.10f", value);
    }
}
