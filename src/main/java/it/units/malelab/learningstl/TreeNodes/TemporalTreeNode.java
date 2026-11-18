/*
 * Provenance: BUSTLE-derived file, modified in STL Repair.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * Original BUSTLE authorship is retained; local changes are identified in git history.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.TreeNodes;

import eu.quanticol.moonlight.formula.Interval;
import eu.quanticol.moonlight.signal.Signal;
import it.units.malelab.jgea.representation.tree.Tree;

import java.util.List;
import java.util.Locale;
import java.util.stream.Collectors;


public abstract class TemporalTreeNode extends AbstractTreeNode {

    protected double start;
    protected double end;

    public TemporalTreeNode(List<Tree<String>> siblings, String message) {
        super();
        this.equipTemporalOperator(siblings, message);
    }

    public Interval createInterval() {
        return new Interval(this.start, this.end);
    }

    /** Materialize an unbounded interval against the finite trace at runtime. */
    public Interval createInterval(Signal<?> signal) {
        if (Double.isFinite(this.end)) {
            return this.createInterval();
        }
        double traceSpan = signal.isEmpty() ? this.start : signal.end() - signal.start();
        return new Interval(this.start, Math.max(this.start, traceSpan));
    }

    public void setInterval(double s, double e) {
        this.start = s;
        this.end = e;
    }

    public double getStart() {
        return this.start;
    }

    public double getEnd() {
        return this.end;
    }

    public String getSymbol() {
        return super.getSymbol() + " I=[" + formatDouble(this.start) + ", " + formatDouble(this.end) + "]";
    }

    private void equipTemporalOperator(List<Tree<String>> siblings, String message) {
        double start;
        double end;
        if (!siblings.get(1).content().equals("const_num")) {
            start = this.parseIntervalNode(siblings.get(1));
            end = start + Math.max(0.0, this.parseIntervalNode(siblings.get(2)));
        }
        else {
            start = 0;
            end = 0;
        }
        this.setInterval(start, end);
        this.symbol = message.toUpperCase().replace(".", "");
    }

    private double parseIntervalNode(Tree<String> node) {
        List<Tree<String>> children = node.childStream().collect(Collectors.toList());
        if (!children.isEmpty() && children.get(0).content().equals("literal_num")) {
            if (children.size() > 1) {
                return parseDouble(children.get(1).content());
            }
            if (!children.get(0).childStream().collect(Collectors.toList()).isEmpty()) {
                return parseDouble(children.get(0).child(0).content());
            }
        }
        return this.parseIntervalBound(children);
    }

    private double parseIntervalBound(List<Tree<String>> leaves) {
        int k = leaves.size() - 1;
        double value = 0.0;
        for (Tree<String> leaf : leaves) {
            value += parseDouble(leaf.child(0).content()) * Math.pow(10, k--);
        }
        return value;
    }

    private String formatDouble(double value) {
        if (value == Double.POSITIVE_INFINITY) {
            return "inf";
        }
        String text = String.format(Locale.US, "%.12f", value);
        text = text.replaceFirst("0+$", "").replaceFirst("\\.$", "");
        if (text.equals("-0")) {
            return "0";
        }
        return text;
    }

    private double parseDouble(String text) {
        if (text.equalsIgnoreCase("inf") || text.equalsIgnoreCase("infinity")) {
            return Double.POSITIVE_INFINITY;
        }
        return Double.parseDouble(text);
    }

}
