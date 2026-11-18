/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This file builds on BUSTLE-derived interfaces but is a repository-specific extension.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.Repair.localization;

import eu.quanticol.moonlight.signal.Signal;
import it.units.malelab.learningstl.TreeNodes.AbstractTreeNode;
import it.units.malelab.learningstl.TreeNodes.AndTreeNode;
import it.units.malelab.learningstl.TreeNodes.BinaryTemporalTreeNode;
import it.units.malelab.learningstl.TreeNodes.ImpliesTreeNode;
import it.units.malelab.learningstl.TreeNodes.NotTreeNode;
import it.units.malelab.learningstl.TreeNodes.OrTreeNode;
import it.units.malelab.learningstl.TreeNodes.TemporalTreeNode;
import it.units.malelab.learningstl.TreeNodes.UnaryTemporalTreeNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Full per-node robustness table plus provenance-oriented backward slicing. */
public final class MonitorEvidence {

    public static final class NodeSeries {
        public final String path;
        public final String type;
        public final AbstractTreeNode node;
        public final double[] robustness;
        public final List<TemporalCell> cells;

        private NodeSeries(
                String path,
                String type,
                AbstractTreeNode node,
                double[] robustness,
                List<TemporalCell> cells
        ) {
            this.path = path;
            this.type = type;
            this.node = node;
            this.robustness = robustness;
            this.cells = cells;
        }
    }

    private final double[] times;
    private final Map<String, NodeSeries> series;
    private final int selfCoefficientCap;
    private static final double DEPTH_FLOOR = 1.0e-12;

    private MonitorEvidence(
            double[] times,
            Map<String, NodeSeries> series,
            int selfCoefficientCap
    ) {
        this.times = times;
        this.series = series;
        this.selfCoefficientCap = Math.max(1, selfCoefficientCap);
    }

    public static MonitorEvidence evaluate(
            AbstractTreeNode root,
            Signal<Map<String, Double>> trace,
            int selfCoefficientCap,
            Map<String, Double> signalScales
    ) {
        double[] times = trace.getTimeSet().stream().sorted().mapToDouble(Double::doubleValue).toArray();
        Map<String, NodeSeries> table = new LinkedHashMap<>();
        collect(root, "root", trace, times, table, signalScales);
        return new MonitorEvidence(times, table, selfCoefficientCap);
    }

    private static void collect(
            AbstractTreeNode node,
            String path,
            Signal<Map<String, Double>> trace,
            double[] times,
            Map<String, NodeSeries> table,
            Map<String, Double> signalScales
    ) {
        if (node.getFirstChild() != null && node.getSecondChild() != null) {
            collect(node.getFirstChild(), path + "/left", trace, times, table, signalScales);
            collect(node.getSecondChild(), path + "/right", trace, times, table, signalScales);
        } else if (node.getFirstChild() != null) {
            collect(node.getFirstChild(), path + "/child", trace, times, table, signalScales);
        } else if (node.getSecondChild() != null) {
            collect(node.getSecondChild(), path + "/child", trace, times, table, signalScales);
        }
        double[] values = normalizedValues(node, path, trace, times, table, signalScales);
        table.put(path, new NodeSeries(path, nodeType(node), node, values, buildCells(times, values)));
    }

    private static double[] normalizedValues(
            AbstractTreeNode node,
            String path,
            Signal<Map<String, Double>> trace,
            double[] times,
            Map<String, NodeSeries> table,
            Map<String, Double> signalScales
    ) {
        if (node.getFirstChild() == null && node.getSecondChild() == null) {
            double[] values = monitoredValues(node, trace, times);
            String variable = node.getVariables().isEmpty() ? "" : node.getVariables().get(0)[0];
            double scale = Math.max(DEPTH_FLOOR, signalScales.getOrDefault(variable, 1.0));
            for (int i = 0; i < values.length; i++) {
                if (Double.isFinite(values[i])) {
                    values[i] /= scale;
                }
            }
            return values;
        }
        NodeSeries child = table.get(path + "/child");
        NodeSeries left = table.get(path + "/left");
        NodeSeries right = table.get(path + "/right");
        double[] values = combine(
                node, times,
                child == null ? null : child.robustness,
                left == null ? null : left.robustness,
                right == null ? null : right.robustness
        );
        return values == null ? monitoredValues(node, trace, times) : values;
    }

    /** Robustness of an operator node from its children's series; null for an unsupported operator. */
    private static double[] combine(
            AbstractTreeNode node,
            double[] times,
            double[] childValues,
            double[] leftValues,
            double[] rightValues
    ) {
        double[] values = new double[times.length];
        java.util.Arrays.fill(values, Double.NaN);
        if (node instanceof NotTreeNode && childValues != null) {
            for (int i = 0; i < values.length; i++) {
                values[i] = finite(childValues[i]) ? -childValues[i] : Double.NaN;
            }
        } else if ((node instanceof AndTreeNode || node instanceof OrTreeNode
                || node instanceof ImpliesTreeNode) && leftValues != null && rightValues != null) {
            for (int i = 0; i < values.length; i++) {
                if (!finite(leftValues[i]) || !finite(rightValues[i])) {
                    continue;
                }
                if (node instanceof AndTreeNode) {
                    values[i] = Math.min(leftValues[i], rightValues[i]);
                } else if (node instanceof OrTreeNode) {
                    values[i] = Math.max(leftValues[i], rightValues[i]);
                } else {
                    values[i] = Math.max(-leftValues[i], rightValues[i]);
                }
            }
        } else if (node instanceof UnaryTemporalTreeNode && childValues != null) {
            TemporalTreeNode temporal = (TemporalTreeNode) node;
            boolean globally = node.getSymbol().startsWith("GLOBALLY");
            for (int i = 0; i < values.length; i++) {
                int from = firstIndexAtOrAfter(times, times[i] + temporal.getStart());
                int to = lastIndexAtOrBefore(times, Math.min(times[times.length - 1], times[i] + temporal.getEnd()));
                values[i] = windowExtreme(childValues, from, to, globally);
            }
        } else if (node instanceof BinaryTemporalTreeNode && leftValues != null && rightValues != null
                && node.getSymbol().startsWith("UNTIL")) {
            TemporalTreeNode temporal = (TemporalTreeNode) node;
            for (int i = 0; i < values.length; i++) {
                int from = firstIndexAtOrAfter(times, times[i] + temporal.getStart());
                int to = lastIndexAtOrBefore(times, Math.min(times[times.length - 1], times[i] + temporal.getEnd()));
                double best = Double.NEGATIVE_INFINITY;
                for (int witness = from; witness >= 0 && witness <= to; witness++) {
                    double prefix = witness == i
                            ? Double.POSITIVE_INFINITY
                            : windowExtreme(leftValues, i, witness - 1, true);
                    if (finite(prefix) && finite(rightValues[witness])) {
                        best = Math.max(best, Math.min(prefix, rightValues[witness]));
                    }
                }
                values[i] = best == Double.NEGATIVE_INFINITY ? Double.NaN : best;
            }
        } else {
            return null;
        }
        return values;
    }

    private static double[] monitoredValues(
            AbstractTreeNode node,
            Signal<Map<String, Double>> trace,
            double[] times
    ) {
        double[] values = new double[times.length];
        java.util.Arrays.fill(values, Double.NaN);
        try {
            Signal<Double> output = node.getOperator().apply(trace).monitor(trace);
            for (int i = 0; i < times.length; i++) {
                if (!output.isEmpty() && times[i] >= output.start() && times[i] <= output.end()) {
                    Double value = output.valueAt(times[i]);
                    values[i] = value == null ? Double.NaN : value;
                }
            }
        } catch (RuntimeException ignored) {
            // Invalid suffixes stay NaN and are excluded from cells and slices.
        }
        return values;
    }

    private static double windowExtreme(double[] values, int from, int to, boolean minimum) {
        if (from < 0 || to < from) {
            return Double.NaN;
        }
        double result = minimum ? Double.POSITIVE_INFINITY : Double.NEGATIVE_INFINITY;
        boolean found = false;
        for (int i = from; i <= to; i++) {
            if (finite(values[i])) {
                result = minimum ? Math.min(result, values[i]) : Math.max(result, values[i]);
                found = true;
            }
        }
        return found ? result : Double.NaN;
    }

    private static boolean finite(double value) {
        return Double.isFinite(value);
    }

    private static int firstIndexAtOrAfter(double[] times, double time) {
        for (int i = 0; i < times.length; i++) {
            if (times[i] >= time) {
                return i;
            }
        }
        return -1;
    }

    private static int lastIndexAtOrBefore(double[] times, double time) {
        for (int i = times.length - 1; i >= 0; i--) {
            if (times[i] <= time) {
                return i;
            }
        }
        return -1;
    }

    private static List<TemporalCell> buildCells(double[] times, double[] values) {
        List<TemporalCell> cells = new ArrayList<>();
        int start = -1;
        boolean truth = false;
        for (int i = 0; i < values.length; i++) {
            if (!Double.isFinite(values[i])) {
                if (start >= 0) {
                    cells.add(cell(times, values, start, i - 1, truth));
                    start = -1;
                }
                continue;
            }
            boolean current = values[i] >= 0.0;
            if (start < 0) {
                start = i;
                truth = current;
            } else if (truth != current) {
                cells.add(cell(times, values, start, i - 1, truth));
                start = i;
                truth = current;
            }
        }
        if (start >= 0) {
            cells.add(cell(times, values, start, values.length - 1, truth));
        }
        return cells;
    }

    private static TemporalCell cell(double[] times, double[] values, int start, int end, boolean truth) {
        int representative = start;
        for (int i = start + 1; i <= end; i++) {
            if ((truth && values[i] > values[representative])
                    || (!truth && values[i] < values[representative])) {
                representative = i;
            }
        }
        return new TemporalCell(
                start, end, times[start], times[end], truth,
                representative, times[representative], values[representative]
        );
    }

    public List<EvidenceItem> backwardSlice(boolean desiredTruth) {
        Map<String, Set<Integer>> relevantTimes = relevantTimes();
        Map<String, double[]> responsibility = new LinkedHashMap<>();
        for (String path : series.keySet()) {
            responsibility.put(path, new double[times.length]);
        }
        responsibility.get("root")[0] = 1.0;

        List<String> paths = new ArrayList<>(series.keySet());
        paths.sort(java.util.Comparator.comparingInt(MonitorEvidence::pathDepth));
        for (String path : paths) {
            boolean target = targetTruth(path, desiredTruth);
            for (int timeIndex : relevantTimes.getOrDefault(path, Set.of())) {
                double incoming = responsibility.get(path)[timeIndex];
                if (incoming > 0.0) {
                    propagate(path, timeIndex, target, incoming, responsibility);
                }
            }
        }

        Map<String, Double> rawByNode = new LinkedHashMap<>();
        List<EvidenceItem> rawItems = new ArrayList<>();
        for (String path : paths) {
            NodeSeries node = series.get(path);
            boolean target = targetTruth(path, desiredTruth);
            double coefficient = atomCoefficient(path, desiredTruth, relevantTimes);
            for (int timeIndex : relevantTimes.getOrDefault(path, Set.of())) {
                double siteResponsibility = responsibility.get(path)[timeIndex];
                if (siteResponsibility <= 0.0) {
                    continue;
                }
                double localCoefficient = node.node.getFirstChild() == null
                        && node.node.getSecondChild() == null
                        ? coefficient
                        : selfCoefficient(path, timeIndex);
                double raw = localCoefficient * siteResponsibility;
                rawByNode.merge(path, raw, Double::sum);
                rawItems.add(new EvidenceItem(
                        path, node.type, times[timeIndex], 0.0,
                        siteResponsibility, localCoefficient, raw,
                        target, truth(path, timeIndex), symptom(node.node)
                ));
            }
        }
        double total = rawByNode.values().stream().mapToDouble(Double::doubleValue).sum();
        if (total <= 0.0) {
            return List.of();
        }
        List<EvidenceItem> normalized = new ArrayList<>();
        for (EvidenceItem item : rawItems) {
            normalized.add(new EvidenceItem(
                    item.nodePath, item.nodeType, item.time,
                    item.rawNodeContribution / total,
                    item.siteResponsibility, item.selfCoefficient,
                    item.rawNodeContribution, item.targetTruth,
                    item.actualTruth, item.symptom
            ));
        }
        return normalized;
    }

    private List<TemporalCell> clippedCells(NodeSeries child, double from, double to) {
        List<TemporalCell> clipped = new ArrayList<>();
        if (child == null || times.length == 0 || to < from) {
            return clipped;
        }
        int windowStart = firstIndexAtOrAfter(from);
        int windowEnd = lastIndexAtOrBefore(to);
        if (windowStart < 0 || windowEnd < windowStart) {
            return clipped;
        }
        for (TemporalCell cell : child.cells) {
            int start = Math.max(windowStart, cell.startIndex);
            int end = Math.min(windowEnd, cell.endIndex);
            if (start <= end) {
                clipped.add(cell(times, child.robustness, start, end, cell.truth));
            }
        }
        return clipped;
    }

    private void propagate(
            String path,
            int timeIndex,
            boolean target,
            double incoming,
            Map<String, double[]> responsibility
    ) {
        AbstractTreeNode node = series.get(path).node;
        if (node.getFirstChild() == null && node.getSecondChild() == null) {
            return;
        }
        if (node instanceof NotTreeNode) {
            pass(responsibility, path + "/child", timeIndex, incoming);
            return;
        }
        if (node instanceof AndTreeNode || node instanceof OrTreeNode) {
            boolean and = node instanceof AndTreeNode;
            List<Site> sites = new ArrayList<>();
            for (String child : List.of(path + "/left", path + "/right")) {
                if (truth(child, timeIndex) != target) {
                    sites.add(new Site(child, timeIndex));
                }
            }
            boolean mandatory = (and && target) || (!and && !target);
            passSites(responsibility, sites, incoming, mandatory);
            return;
        }
        if (node instanceof ImpliesTreeNode) {
            List<Site> sites = new ArrayList<>();
            String left = path + "/left";
            String right = path + "/right";
            if (target) {
                sites.add(new Site(left, timeIndex));
                sites.add(new Site(right, timeIndex));
                passSites(responsibility, sites, incoming, false);
            } else {
                if (!truth(left, timeIndex)) {
                    sites.add(new Site(left, timeIndex));
                }
                if (truth(right, timeIndex)) {
                    sites.add(new Site(right, timeIndex));
                }
                passSites(responsibility, sites, incoming, true);
            }
            return;
        }
        if (node instanceof UnaryTemporalTreeNode && node instanceof TemporalTreeNode) {
            TemporalTreeNode temporal = (TemporalTreeNode) node;
            String child = path + "/child";
            List<Site> sites = new ArrayList<>();
            for (int index : window(timeIndex, temporal.getStart(), temporal.getEnd(), false)) {
                if (truth(child, index) != target) {
                    sites.add(new Site(child, index));
                }
            }
            boolean globally = node.getSymbol().startsWith("GLOBALLY");
            boolean mandatory = (globally && target) || (!globally && !target);
            passSites(responsibility, sites, incoming, mandatory);
            return;
        }
        if (node instanceof BinaryTemporalTreeNode && node.getSymbol().startsWith("UNTIL")) {
            passRepairFamily(responsibility, untilRepairs(path, timeIndex, target), incoming);
        }
    }

    private void passSites(
            Map<String, double[]> responsibility,
            List<Site> sites,
            double incoming,
            boolean mandatory
    ) {
        if (sites.isEmpty()) {
            return;
        }
        if (mandatory) {
            double share = incoming / sites.size();
            for (Site site : sites) {
                pass(responsibility, site.path, site.timeIndex, share);
            }
            return;
        }
        double denominator = sites.stream().mapToDouble(site -> 1.0 / effort(site)).sum();
        for (Site site : sites) {
            pass(responsibility, site.path, site.timeIndex,
                    incoming * (1.0 / effort(site)) / denominator);
        }
    }

    private void passRepairFamily(
            Map<String, double[]> responsibility,
            List<List<Site>> repairs,
            double incoming
    ) {
        if (repairs.isEmpty()) {
            return;
        }
        double denominator = repairs.stream().mapToDouble(repair -> 1.0 / repairEffort(repair)).sum();
        for (List<Site> repair : repairs) {
            double probability = (1.0 / repairEffort(repair)) / denominator;
            double share = incoming * probability / repair.size();
            for (Site site : repair) {
                pass(responsibility, site.path, site.timeIndex, share);
            }
        }
    }

    private List<List<Site>> untilRepairs(String path, int timeIndex, boolean target) {
        TemporalTreeNode temporal = (TemporalTreeNode) series.get(path).node;
        String left = path + "/left";
        String right = path + "/right";
        List<Integer> witnessTimes = window(timeIndex, temporal.getStart(), temporal.getEnd(), false);
        List<List<Site>> repairs = new ArrayList<>();
        if (target) {
            for (int witness : witnessTimes) {
                List<Site> repair = new ArrayList<>();
                if (!truth(right, witness)) {
                    repair.add(new Site(right, witness));
                }
                for (int index = timeIndex; index < witness; index++) {
                    if (!truth(left, index)) {
                        repair.add(new Site(left, index));
                    }
                }
                if (!repair.isEmpty()) {
                    repairs.add(repair);
                }
            }
        } else {
            List<Integer> witnesses = new ArrayList<>();
            for (int witness : witnessTimes) {
                boolean valid = truth(right, witness);
                for (int index = timeIndex; valid && index < witness; index++) {
                    valid = truth(left, index);
                }
                if (valid) {
                    witnesses.add(witness);
                }
            }
            if (!witnesses.isEmpty()) {
                int lastWitness = witnesses.get(witnesses.size() - 1);
                for (int index = timeIndex; index < lastWitness; index++) {
                    List<Site> repair = new ArrayList<>();
                    repair.add(new Site(left, index));
                    for (int witness : witnesses) {
                        if (witness <= index) {
                            repair.add(new Site(right, witness));
                        }
                    }
                    repairs.add(repair);
                }
                List<Site> allWitnesses = new ArrayList<>();
                for (int witness : witnesses) {
                    allWitnesses.add(new Site(right, witness));
                }
                repairs.add(allWitnesses);
            }
        }
        return minimalRepairs(repairs);
    }

    private List<List<Site>> minimalRepairs(List<List<Site>> repairs) {
        List<List<Site>> unique = new ArrayList<>();
        for (List<Site> repair : repairs) {
            List<Site> normalized = new ArrayList<>(new LinkedHashSet<>(repair));
            if (!normalized.isEmpty() && unique.stream().noneMatch(existing -> sameSites(existing, normalized))) {
                unique.add(normalized);
            }
        }
        unique.removeIf(candidate -> unique.stream().anyMatch(other -> other != candidate
                && candidate.size() > other.size() && candidate.containsAll(other)));
        return unique;
    }

    private boolean sameSites(List<Site> first, List<Site> second) {
        return first.size() == second.size() && first.containsAll(second);
    }

    private double repairEffort(List<Site> repair) {
        return Math.max(DEPTH_FLOOR, repair.stream().mapToDouble(this::effort).sum());
    }

    private double effort(Site site) {
        return 1.0;
    }

    private void pass(Map<String, double[]> responsibility, String path, int timeIndex, double amount) {
        double[] values = responsibility.get(path);
        if (values != null && timeIndex >= 0 && timeIndex < values.length && Double.isFinite(amount)) {
            values[timeIndex] += amount;
        }
    }

    private Map<String, Set<Integer>> relevantTimes() {
        Map<String, Set<Integer>> relevant = new LinkedHashMap<>();
        for (String path : series.keySet()) {
            relevant.put(path, new LinkedHashSet<>());
        }
        relevant.get("root").add(0);
        List<String> paths = new ArrayList<>(series.keySet());
        paths.sort(java.util.Comparator.comparingInt(MonitorEvidence::pathDepth));
        for (String path : paths) {
            AbstractTreeNode node = series.get(path).node;
            for (int timeIndex : new ArrayList<>(relevant.get(path))) {
                if (node instanceof UnaryTemporalTreeNode && node instanceof TemporalTreeNode) {
                    TemporalTreeNode temporal = (TemporalTreeNode) node;
                    relevant.get(path + "/child").addAll(
                            window(timeIndex, temporal.getStart(), temporal.getEnd(), false));
                } else if (node instanceof BinaryTemporalTreeNode && node.getSymbol().startsWith("UNTIL")) {
                    TemporalTreeNode temporal = (TemporalTreeNode) node;
                    relevant.get(path + "/right").addAll(
                            window(timeIndex, temporal.getStart(), temporal.getEnd(), false));
                    relevant.get(path + "/left").addAll(
                            window(timeIndex, 0.0, temporal.getEnd(), true));
                } else if (node.getFirstChild() != null && node.getSecondChild() != null) {
                    relevant.get(path + "/left").add(timeIndex);
                    relevant.get(path + "/right").add(timeIndex);
                } else if (node.getFirstChild() != null || node.getSecondChild() != null) {
                    relevant.get(path + "/child").add(timeIndex);
                }
            }
        }
        return relevant;
    }

    private List<Integer> window(int timeIndex, double start, double end, boolean endExclusive) {
        List<Integer> indexes = new ArrayList<>();
        double from = times[timeIndex] + start;
        double to = times[timeIndex] + end;
        for (int i = 0; i < times.length; i++) {
            if (times[i] >= from && (endExclusive ? times[i] < to : times[i] <= to)) {
                indexes.add(i);
            }
        }
        return indexes;
    }

    /**
     * Atom credit (Eq. 17), counterfactual form: complement the atom on this trace, re-monitor
     * the formula, and credit the atom with K if the root verdict then equals the label, else 1.
     * K does not depend on how many times the atom is relevant: a single edit that explains the
     * counterexample is the cheapest explanation wherever the atom sits.
     */
    private double atomCoefficient(String path, boolean rootTarget, Map<String, Set<Integer>> relevantTimes) {
        NodeSeries node = series.get(path);
        if (node.node.getFirstChild() != null || node.node.getSecondChild() != null) {
            return 1.0;
        }
        Set<Integer> indexes = relevantTimes.getOrDefault(path, Set.of());
        return !indexes.isEmpty() && complementFixesVerdict(path, rootTarget) ? selfCoefficientCap : 1.0;
    }

    private boolean complementFixesVerdict(String atomPath, boolean rootTarget) {
        Map<String, double[]> values = new LinkedHashMap<>();
        // series is in post-order (children first), so one pass recomputes every ancestor.
        for (Map.Entry<String, NodeSeries> entry : series.entrySet()) {
            String path = entry.getKey();
            double[] original = entry.getValue().robustness;
            if (path.equals(atomPath)) {
                double[] complemented = new double[original.length];
                for (int i = 0; i < original.length; i++) {
                    complemented[i] = finite(original[i]) ? -original[i] : Double.NaN;
                }
                values.put(path, complemented);
            } else if (atomPath.startsWith(path + "/")) {
                double[] recomputed = combine(
                        entry.getValue().node, times,
                        values.get(path + "/child"), values.get(path + "/left"), values.get(path + "/right")
                );
                if (recomputed == null) {
                    return false;
                }
                values.put(path, recomputed);
            } else {
                values.put(path, original);
            }
        }
        double root = values.get("root")[0];
        return finite(root) && (root >= 0.0) == rootTarget;
    }

    private double selfCoefficient(String path, int timeIndex) {
        NodeSeries current = series.get(path);
        if (!(current.node instanceof UnaryTemporalTreeNode)
                || !(current.node instanceof TemporalTreeNode)) {
            return 1.0;
        }
        TemporalTreeNode temporal = (TemporalTreeNode) current.node;
        String child = path + "/child";
        List<Integer> indexes = window(timeIndex, temporal.getStart(), temporal.getEnd(), false);
        long trueCount = indexes.stream().filter(index -> truth(child, index)).count();
        long falseCount = indexes.size() - trueCount;
        if (current.node.getSymbol().startsWith("GLOBALLY")
                && !truth(path, timeIndex) && falseCount < indexes.size()) {
            return Math.min(falseCount, selfCoefficientCap);
        }
        if (current.node.getSymbol().startsWith("EVENTUALLY")
                && truth(path, timeIndex) && trueCount < indexes.size()) {
            return Math.min(trueCount, selfCoefficientCap);
        }
        return 1.0;
    }

    private boolean targetTruth(String path, boolean rootTarget) {
        boolean target = rootTarget;
        String current = "root";
        if (path.equals(current)) {
            return target;
        }
        for (String part : path.substring("root/".length()).split("/")) {
            AbstractTreeNode parent = series.get(current).node;
            if (parent instanceof NotTreeNode
                    || (parent instanceof ImpliesTreeNode && part.equals("left"))) {
                target = !target;
            }
            current += "/" + part;
        }
        return target;
    }

    private static int pathDepth(String path) {
        return path.equals("root") ? 0 : path.split("/").length - 1;
    }

    private String symptom(AbstractTreeNode node) {
        if (node instanceof AndTreeNode) return "boolean_conjunction";
        if (node instanceof OrTreeNode) return "boolean_disjunction";
        if (node instanceof ImpliesTreeNode) return "implication";
        if (node instanceof NotTreeNode) return "negation_path";
        if (node instanceof UnaryTemporalTreeNode) {
            return node.getSymbol().startsWith("GLOBALLY")
                    ? "invariance_constraint" : "reachability_constraint";
        }
        if (node instanceof BinaryTemporalTreeNode) return "until_constraint";
        if (node.getFirstChild() == null && node.getSecondChild() == null) {
            return "predicate_verdict_mismatch";
        }
        return "structural_constraint";
    }

    private static final class Site {
        final String path;
        final int timeIndex;

        Site(String path, int timeIndex) {
            this.path = path;
            this.timeIndex = timeIndex;
        }

        @Override
        public boolean equals(Object other) {
            return other instanceof Site
                    && path.equals(((Site) other).path)
                    && timeIndex == ((Site) other).timeIndex;
        }

        @Override
        public int hashCode() {
            return 31 * path.hashCode() + timeIndex;
        }
    }

    private int firstIndexAtOrAfter(double time) {
        for (int i = 0; i < times.length; i++) {
            if (times[i] >= time) {
                return i;
            }
        }
        return -1;
    }

    private int lastIndexAtOrBefore(double time) {
        for (int i = times.length - 1; i >= 0; i--) {
            if (times[i] <= time) {
                return i;
            }
        }
        return -1;
    }

    private boolean truth(String path, int timeIndex) {
        return value(path, timeIndex) >= 0.0;
    }

    private double value(String path, int timeIndex) {
        NodeSeries item = series.get(path);
        if (item == null || timeIndex < 0 || timeIndex >= item.robustness.length) {
            return Double.NaN;
        }
        return item.robustness[timeIndex];
    }

    private static String nodeType(AbstractTreeNode node) {
        String symbol = node.getSymbol();
        int interval = symbol.indexOf(" I=[");
        return interval < 0 ? symbol : symbol.substring(0, interval);
    }

    public double[] getTimes() {
        return times.clone();
    }

    public Map<String, NodeSeries> getSeries() {
        return java.util.Collections.unmodifiableMap(series);
    }


    public int getSelfCoefficientCap() {
        return selfCoefficientCap;
    }
}
