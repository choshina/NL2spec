/*
 * Provenance: BUSTLE-derived file, modified in STL Repair.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * Original BUSTLE authorship is retained; local changes are identified in git history.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.SignalBuilders;

import eu.quanticol.moonlight.signal.Signal;

import java.io.*;
import java.util.*;


public class SupervisedSignalBuilder implements SignalBuilder<Signal<Map<String, Double>>> {

    private final Map<String, double[]> varsBounds = new HashMap<>();
    private double[] temporalBounds;

    public double[] readVectorFromFile(String filePath) throws IOException {
        BufferedReader reader = this.createReaderFromFile(filePath);
        String[] line;
        try {
            line = reader.readLine().split(",");
        }
        catch (NullPointerException | IOException e) {  // TODO: might pretend that NullPointerException is not there
            return new double[0];
        }
        double[] out = new double[line.length];
        for (int i=0; i < line.length; ++i) out[i] = Double.parseDouble(line[i]);
        reader.close();
        return out;
    }

    public List<Signal<Map<String, Double>>> parseSignals(String path) throws IOException {
        return parseSignals(path, true, false);
    }

    public List<Signal<Map<String, Double>>> parseSignals(String path, boolean normalizeValues, boolean renumberTimes) throws IOException {
        List<Signal<Map<String, Double>>> signals = new ArrayList<>();
        double[] times = readTimesVector(path);
        if (renumberTimes) {
            for (int i=0; i < times.length; ++i) {
                times[i] = i;
            }
        }
        this.temporalBounds = new double[]{times[0], times[times.length - 1]};
        BufferedReader reader = this.createReaderFromFile(path + "/data.csv");
        String[] header = reader.readLine().split(",");
        for (String var : header) {
            this.varsBounds.put(var, new double[]{Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY});
        }
        this.computeVarsBounds(path, header);
        double[] varsData;
        int numVars = header.length;
        Signal<Map<String, Double>> currSignal = new Signal<>();
        while (true) {
            try {
                varsData = Arrays.stream(reader.readLine().split(",")).mapToDouble(Double::parseDouble).toArray();
            }
            catch (NullPointerException | IOException e) {
                break;
            }
            int k = 0;
            for (int i=0; i < varsData.length; i+=numVars) {
                Map<String, Double> trajectoryRecord = new HashMap<>();
                for (int j=0; j < numVars; ++j) {
                    if (normalizeValues) {
                        double[] bounds = this.varsBounds.get(header[j]);
                        double range = bounds[1] - bounds[0];
                        double value = range == 0.0 ? 0.0 : (varsData[i + j] - bounds[0]) / range;
                        trajectoryRecord.put(header[j], value);
                    } else {
                        trajectoryRecord.put(header[j], varsData[i + j]);
                    }
                }
                currSignal.add(times[k++], trajectoryRecord);
            }
            signals.add(currSignal);
            currSignal = new Signal<>();
        }
        reader.close();
        return signals;
    }

    private double[] readTimesVector(String path) throws IOException {
        File preferred = new File(path + "/times.csv");
        if (preferred.exists()) {
            return this.readVectorFromFile(preferred.getPath());
        }
        File fallback = new File(path + "/time.csv");
        if (fallback.exists()) {
            return this.readVectorFromFile(fallback.getPath());
        }
        throw new FileNotFoundException("Missing times.csv/time.csv in " + path);
    }

    private void computeVarsBounds(String path, String[] header) throws IOException {
        BufferedReader reader = this.createReaderFromFile(path + "/data.csv");
        reader.readLine();
        double[] varsData;
        while (true) {
            try {
                varsData = Arrays.stream(reader.readLine().split(",")).mapToDouble(Double::parseDouble).toArray();
            }
            catch (NullPointerException | IOException e) {
                break;
            }
            for (int i=0; i < varsData.length; i+=header.length) {
                for (int j=0; j < header.length; ++j) {
                    double[] temp = this.varsBounds.get(header[j]);
                    temp[0] = Math.min(varsData[i + j], temp[0]);
                    temp[1] = Math.max(varsData[i + j], temp[1]);
                }
            }
        }
        reader.close();
    }

    public Map<String, double[]> getVarsBounds() {
        return this.varsBounds;
    }

    public double[] getTemporalBounds() {
        return this.temporalBounds;
    }

}
