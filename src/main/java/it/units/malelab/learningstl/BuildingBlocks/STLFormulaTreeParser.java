/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This file builds on BUSTLE-derived interfaces but is a repository-specific extension.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks;

import it.units.malelab.jgea.representation.tree.Tree;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class STLFormulaTreeParser {

    private static final String NUMBER = "-?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?";
    private static final String TEMPORAL_BOUND = "(?:" + NUMBER + "|inf|Infinity)";
    private static final Pattern ATOM = Pattern.compile("^\\s*([A-Za-z_]\\w*)\\s*(<=|>=|==|=|[<>])\\s*(" + NUMBER + ")\\s*$");
    private static final Pattern BOOL_ATOM = Pattern.compile("^\\s*([A-Za-z_]\\w*)\\s*$");
    private static final Pattern SHORT_TEMPORAL = Pattern.compile("^\\s*([GF])\\s*\\[\\s*(" + TEMPORAL_BOUND + ")\\s*,\\s*(" + TEMPORAL_BOUND + ")\\s*]\\s*\\((.*)\\)\\s*$", Pattern.DOTALL);
    private static final Pattern INFIX_UNTIL = Pattern.compile("^\\s*(.*)\\s+U\\s*\\[\\s*(" + TEMPORAL_BOUND + ")\\s*,\\s*(" + TEMPORAL_BOUND + ")\\s*]\\s+(.*)\\s*$", Pattern.DOTALL);

    private final boolean useConstPlaceholders;

    private STLFormulaTreeParser(boolean useConstPlaceholders) {
        this.useConstPlaceholders = useConstPlaceholders;
    }

    public static Tree<String> parseFile(Path path, boolean useConstPlaceholders) throws IOException {
        String formula = Files.readString(path);
        return parse(formula, useConstPlaceholders);
    }

    public static Tree<String> parse(String formula, boolean useConstPlaceholders) {
        STLFormulaTreeParser parser = new STLFormulaTreeParser(useConstPlaceholders);
        return parser.expr(parser.stripComments(formula), 1);
    }

    private String stripComments(String formula) {
        List<String> lines = new ArrayList<>();
        for (String line : formula.split("\\R")) {
            String trimmed = line.trim();
            if (!trimmed.isEmpty() && !trimmed.startsWith("#")) {
                lines.add(trimmed);
            }
        }
        return String.join(" ", lines).trim();
    }

    private Tree<String> expr(String formula, int level) {
        formula = stripOuterParens(formula.trim());
        formula = stripOuterSquareBrackets(formula);
        FormulaCall call = prefixCall(formula);
        if (call != null) {
            return switch (call.name) {
                case "AND" -> logic(".and", call.args, level);
                case "OR" -> logic(".or", call.args, level);
                case "IMPLIES" -> implies(call.args, level);
                case "NOT" -> not(call.args, level);
                case "EVENTUALLY" -> unaryTemporal(".eventually", call.intervalStart, call.intervalEnd, call.args, level);
                case "GLOBALLY" -> unaryTemporal(".globally", call.intervalStart, call.intervalEnd, call.args, level);
                case "UNTIL" -> until(call.intervalStart, call.intervalEnd, call.args, level);
                default -> throw new IllegalArgumentException("Unsupported formula operator: " + call.name);
            };
        }

        Matcher shortTemporal = SHORT_TEMPORAL.matcher(formula);
        if (shortTemporal.matches()) {
            String op = shortTemporal.group(1).equals("G") ? ".globally" : ".eventually";
            double start = parseTemporalBound(shortTemporal.group(2));
            double end = parseTemporalBound(shortTemporal.group(3));
            return unaryTemporal(op, start, end, List.of(shortTemporal.group(4)), level);
        }

        Matcher until = INFIX_UNTIL.matcher(formula);
        if (until.matches()) {
            double start = parseTemporalBound(until.group(2));
            double end = parseTemporalBound(until.group(3));
            return until(start, end, List.of(until.group(1), until.group(4)), level);
        }

        Matcher atom = ATOM.matcher(formula);
        if (atom.matches()) {
            String comparator = atom.group(2).equals("=") ? "==" : atom.group(2);
            return prop(atom.group(1), comparator, Double.parseDouble(atom.group(3)), level);
        }
        Matcher boolAtom = BOOL_ATOM.matcher(formula);
        if (boolAtom.matches() && !isReservedWord(boolAtom.group(1))) {
            return boolProp(boolAtom.group(1), level);
        }

        throw new IllegalArgumentException("Cannot parse seeded STL formula fragment: " + formula);
    }

    private String stripOuterSquareBrackets(String formula) {
        formula = formula.trim();
        if (!formula.startsWith("[") || !formula.endsWith("]")) {
            return formula;
        }
        int closing = matchingBracket(formula, 0, '[', ']');
        if (closing == formula.length() - 1) {
            return formula.substring(1, formula.length() - 1).trim();
        }
        return formula;
    }

    private Tree<String> logic(String op, List<String> args, int level) {
        if (args.size() < 2) {
            throw new IllegalArgumentException(op + " requires at least two operands");
        }
        Tree<String> current = binaryLogic(op, args.get(0), args.get(1), level);
        for (int i = 2; i < args.size(); i++) {
            current = wrapExpr(Tree.of(logicSymbol(level), Tree.of(op), current, expr(args.get(i), level)), level);
        }
        return current;
    }

    private Tree<String> binaryLogic(String op, String left, String right, int level) {
        return wrapExpr(Tree.of(logicSymbol(level), Tree.of(op), expr(left, level), expr(right, level)), level);
    }

    /**
     * IMPLIES is binary and non-associative, so unlike AND/OR it takes exactly
     * two operands rather than folding a list: a -> b -> c has no agreed
     * grouping and guessing one silently changes the requirement.
     */
    private Tree<String> implies(List<String> args, int level) {
        if (args.size() != 2) {
            throw new IllegalArgumentException("IMPLIES requires exactly two operands");
        }
        return binaryLogic(".implies", args.get(0), args.get(1), level);
    }

    private Tree<String> not(List<String> args, int level) {
        if (args.size() != 1) {
            throw new IllegalArgumentException("NOT requires one operand");
        }
        return wrapExpr(Tree.of(logicSymbol(level), Tree.of(".not"), expr(args.get(0), level)), level);
    }

    private Tree<String> unaryTemporal(String op, double start, double end, List<String> args, int level) {
        if (args.size() != 1) {
            throw new IllegalArgumentException(op + " requires one operand");
        }
        ensureTemporalAllowed(level);
        return wrapExpr(Tree.of(
                pastSymbol(level),
                Tree.of(op),
                expr(args.get(0), level + 1),
                intervalStart(start),
                intervalLength(start, end)
        ), level);
    }

    private Tree<String> until(double start, double end, List<String> args, int level) {
        if (args.size() != 2) {
            throw new IllegalArgumentException("UNTIL requires two operands");
        }
        ensureTemporalAllowed(level);
        return wrapExpr(Tree.of(
                pastSymbol(level),
                Tree.of(".until"),
                expr(args.get(0), level + 1),
                intervalStart(start),
                intervalLength(start, end),
                expr(args.get(1), level + 1)
        ), level);
    }

    private Tree<String> prop(String variable, String comparison, double value, int level) {
        return wrapExpr(Tree.of(
                "<prop>",
                Tree.of(".prop"),
                Tree.of("<num_var>", Tree.of(variable)),
                Tree.of("<comp>", Tree.of(comparison)),
                number(value)
        ), level);
    }

    private Tree<String> boolProp(String variable, int level) {
        return wrapExpr(Tree.of(
                "<bool_prop>",
                Tree.of(".bool"),
                Tree.of("<bool_var>", Tree.of(variable))
        ), level);
    }

    private boolean isReservedWord(String value) {
        return List.of(
                "AND", "OR", "NOT", "IMPLIES", "EVENTUALLY", "GLOBALLY", "UNTIL",
                "TRUE", "FALSE", "I", "G", "F", "U", "and", "or", "not"
        ).contains(value);
    }

    private Tree<String> number(double value) {
        if (useConstPlaceholders) {
            return Tree.of("const_num");
        }
        return literalNumber("<num>", value);
    }

    private Tree<String> intervalStart(double start) {
        if (useConstPlaceholders) {
            return Tree.of("const_num");
        }
        return literalNumber("<interval_symbol>", start);
    }

    private Tree<String> intervalLength(double start, double end) {
        if (useConstPlaceholders) {
            return Tree.of("const_num");
        }
        return literalNumber("<interval_symbol>", Math.max(0.0, end - start));
    }

    private Tree<String> literalNumber(String symbol, double value) {
        if (value == Double.POSITIVE_INFINITY) {
            return Tree.of(symbol, Tree.of("literal_num"), Tree.of("inf"));
        }
        if (!Double.isFinite(value)) {
            throw new IllegalArgumentException("Non-finite numeric value in seeded STL: " + value);
        }
        return Tree.of(symbol, Tree.of("literal_num"), Tree.of(formatDouble(value)));
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

    private Tree<String> twoDigitTree(String symbol, int value) {
        if (value < 0 || value > 99) {
            throw new IllegalArgumentException(symbol + " can encode only values in [0,99]: " + value);
        }
        int tens = value / 10;
        int ones = value % 10;
        return Tree.of(
                symbol,
                Tree.of("<digit>", Tree.of(Integer.toString(tens))),
                Tree.of("<digit>", Tree.of(Integer.toString(ones)))
        );
    }

    private Tree<String> wrapExpr(Tree<String> child, int level) {
        return Tree.of(exprSymbol(level), child);
    }

    private void ensureTemporalAllowed(int level) {
        // Formula-seeded runs use the seeded grammars, where third-level temporal operators are recursive.
    }

    private String exprSymbol(int level) {
        return switch (level) {
            case 1 -> "<expr_first_level>";
            case 2 -> "<expr_second_level>";
            default -> "<expr_third_level_onwards>";
        };
    }

    private String logicSymbol(int level) {
        return switch (level) {
            case 1 -> "<logic_first_level>";
            case 2 -> "<logic_second_level>";
            default -> "<logic_third_level_onwards>";
        };
    }

    private String pastSymbol(int level) {
        if (level == 1) {
            return "<past_first_level>";
        }
        if (level == 2) {
            return "<past_second_level>";
        }
        return "<past_third_level_onwards>";
    }

    private FormulaCall prefixCall(String formula) {
        int bracket = formula.indexOf('[');
        int interval = formula.indexOf("I=");
        int nameEnd;
        if (interval >= 0 && (bracket < 0 || interval < bracket)) {
            nameEnd = interval;
        } else {
            nameEnd = bracket;
        }
        if (nameEnd < 0) {
            return null;
        }
        String name = formula.substring(0, nameEnd).trim();
        if (!List.of("AND", "OR", "NOT", "IMPLIES", "EVENTUALLY", "GLOBALLY", "UNTIL").contains(name)) {
            return null;
        }
        if (name.equals("EVENTUALLY") || name.equals("GLOBALLY") || name.equals("UNTIL")) {
            int intervalStart = formula.indexOf('[', formula.indexOf("I="));
            int intervalEnd = matchingBracket(formula, intervalStart, '[', ']');
            List<String> bounds = splitTopLevel(formula.substring(intervalStart + 1, intervalEnd));
            if (bounds.size() != 2) {
                throw new IllegalArgumentException("Invalid interval in " + formula);
            }
            int argsStart = skipWhitespace(formula, intervalEnd + 1);
            int argsEnd = matchingBracket(formula, argsStart, '[', ']');
            if (skipWhitespace(formula, argsEnd + 1) != formula.length()) {
                throw new IllegalArgumentException("Trailing content in formula: " + formula);
            }
            return new FormulaCall(name, splitTopLevel(formula.substring(argsStart + 1, argsEnd)),
                    parseTemporalBound(bounds.get(0)), parseTemporalBound(bounds.get(1)));
        }
        int argsStart = formula.indexOf('[', name.length());
        int argsEnd = matchingBracket(formula, argsStart, '[', ']');
        if (skipWhitespace(formula, argsEnd + 1) != formula.length()) {
            return null;
        }
        return new FormulaCall(name, splitTopLevel(formula.substring(argsStart + 1, argsEnd)), 0, 0);
    }

    private List<String> splitTopLevel(String content) {
        List<String> parts = new ArrayList<>();
        StringBuilder current = new StringBuilder();
        int squareDepth = 0;
        int parenDepth = 0;
        for (char c : content.toCharArray()) {
            if (c == '[') squareDepth++;
            if (c == ']') squareDepth--;
            if (c == '(') parenDepth++;
            if (c == ')') parenDepth--;
            if (c == ',' && squareDepth == 0 && parenDepth == 0) {
                parts.add(current.toString().trim());
                current.setLength(0);
            } else {
                current.append(c);
            }
        }
        if (current.length() > 0) {
            parts.add(current.toString().trim());
        }
        return parts;
    }

    private int matchingBracket(String text, int start, char open, char close) {
        if (start < 0 || start >= text.length() || text.charAt(start) != open) {
            throw new IllegalArgumentException("Expected " + open + " in: " + text);
        }
        int depth = 0;
        for (int i = start; i < text.length(); i++) {
            if (text.charAt(i) == open) depth++;
            if (text.charAt(i) == close) {
                depth--;
                if (depth == 0) return i;
            }
        }
        throw new IllegalArgumentException("Unclosed bracket in: " + text);
    }

    private String stripOuterParens(String formula) {
        while (formula.startsWith("(") && matchingBracket(formula, 0, '(', ')') == formula.length() - 1) {
            formula = formula.substring(1, formula.length() - 1).trim();
        }
        return formula;
    }

    private int skipWhitespace(String text, int index) {
        while (index < text.length() && Character.isWhitespace(text.charAt(index))) {
            index++;
        }
        return index;
    }

    private static double parseTemporalBound(String text) {
        String value = text.trim();
        if (value.equalsIgnoreCase("inf") || value.equalsIgnoreCase("infinity")) {
            return Double.POSITIVE_INFINITY;
        }
        return Double.parseDouble(value);
    }

    private static class FormulaCall {
        private final String name;
        private final List<String> args;
        private final double intervalStart;
        private final double intervalEnd;

        private FormulaCall(String name, List<String> args, double intervalStart, double intervalEnd) {
            this.name = name;
            this.args = args;
            this.intervalStart = intervalStart;
            this.intervalEnd = intervalEnd;
        }
    }
}
