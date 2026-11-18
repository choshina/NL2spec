/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This file builds on BUSTLE-derived interfaces but is a repository-specific extension.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.Repair.localization;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Minimal RFC-4180 reader for repair trace metadata, indexed by data.csv row. */
public final class TraceMetadataIndex {

    public static final class Entry {
        public final String traceId;
        public final String roundId;
        public final String source;
        public final String ceClassId;

        private Entry(String traceId, String roundId, String source, String ceClassId) {
            this.traceId = traceId;
            this.roundId = roundId;
            this.source = source;
            this.ceClassId = ceClassId;
        }
    }

    private final List<Entry> rows;

    private TraceMetadataIndex(List<Entry> rows) {
        this.rows = rows;
    }

    public static TraceMetadataIndex load(Path path) {
        if (path == null || !Files.isRegularFile(path)) {
            return new TraceMetadataIndex(List.of());
        }
        try {
            List<String> lines = Files.readAllLines(path);
            if (lines.isEmpty()) {
                return new TraceMetadataIndex(List.of());
            }
            List<String> header = parseCsvLine(lines.get(0));
            Map<String, Integer> columns = new LinkedHashMap<>();
            for (int i = 0; i < header.size(); i++) {
                columns.put(header.get(i), i);
            }
            List<Entry> rows = new ArrayList<>();
            for (int i = 1; i < lines.size(); i++) {
                List<String> values = parseCsvLine(lines.get(i));
                rows.add(new Entry(
                        value(values, columns.get("trace_id"), String.format("trace_%06d", i)),
                        value(values, columns.get("round_id"), ""),
                        value(values, columns.get("source"), "unknown"),
                        value(values, columns.get("ceclass_id"), "")
                ));
            }
            return new TraceMetadataIndex(rows);
        } catch (IOException exception) {
            System.err.println("WARNING: cannot read repair trace metadata: " + exception.getMessage());
            return new TraceMetadataIndex(List.of());
        }
    }

    public Entry get(int sourceIndex) {
        if (sourceIndex >= 0 && sourceIndex < rows.size()) {
            return rows.get(sourceIndex);
        }
        int displayIndex = Math.max(0, sourceIndex) + 1;
        return new Entry(String.format("trace_%06d", displayIndex), "", "unknown", "");
    }

    public String groupId(int sourceIndex, int predicted, int expected) {
        Entry entry = get(sourceIndex);
        String ceClass = entry.ceClassId.isBlank() ? "no_ceclass" : entry.ceClassId;
        return predicted + "->" + expected + "|" + ceClass;
    }

    private static String value(List<String> values, Integer index, String fallback) {
        return index != null && index >= 0 && index < values.size() && !values.get(index).isBlank()
                ? values.get(index)
                : fallback;
    }

    private static List<String> parseCsvLine(String line) {
        List<String> fields = new ArrayList<>();
        StringBuilder current = new StringBuilder();
        boolean quoted = false;
        for (int i = 0; i < line.length(); i++) {
            char ch = line.charAt(i);
            if (ch == '"') {
                if (quoted && i + 1 < line.length() && line.charAt(i + 1) == '"') {
                    current.append('"');
                    i++;
                } else {
                    quoted = !quoted;
                }
            } else if (ch == ',' && !quoted) {
                fields.add(current.toString());
                current.setLength(0);
            } else {
                current.append(ch);
            }
        }
        fields.add(current.toString());
        return fields;
    }
}
