% Provenance: STL Repair project-specific integration component.
% Calls the ATG-lineage STL_TO_SMT code and vendored Breach toolbox.
% See README.md, ../STL_TO_SMT/UPSTREAM.md, and ../../THIRD_PARTY.md.
%
% Generate STL traces from formula CSV files.
%
% Default input:
%   dataset/case_formulas.csv, row 1, column "formula"
%   dataset/oracle_formulas.csv, row 1, column "formula"
%
% Default output:
%   dataset/generated/time.csv
%   dataset/generated/data.csv
%   dataset/generated/case_label.csv
%   dataset/generated/oracle_label.csv
%
% Run from MATLAB:
%   GenerateCaseDatasetFromCsv
%
% Or with overrides:
%   GenerateCaseDatasetFromCsv('row', 3, 'exportDir', fullfile('dataset', 'generated'))

function GenerateCaseDatasetFromCsv(varargin)
    cfg = parse_options(varargin{:});
    repo_root = resolve_repo_root();
    setup_paths(repo_root);

    formula_csv = resolve_repo_path(repo_root, cfg.formulaCsv);
    formula = read_formula_from_csv(formula_csv, cfg.row, cfg.formulaColumn);
    oracle_formula_csv = resolve_repo_path(repo_root, cfg.oracleFormulaCsv);
    oracle_formula = read_formula_from_csv(oracle_formula_csv, cfg.oracleRow, cfg.oracleFormulaColumn);
    formula_used = formula;
    if strcmp(cfg.formulaDialect, 'breach')
        breach_formula = formula_used;
    else
        breach_formula = bustle_to_breach(formula_used);
    end
    domain_horizon = max(cfg.sampleTimes) - min(cfg.sampleTimes);
    generation_formula = augment_formula_for_all_signals(breach_formula, cfg.signalNames, cfg.domainBounds, domain_horizon, cfg.booleanSignalNames, cfg.discreteSignalNames, cfg.derivedSignalDefinitions);
    violation_formula = augment_formula_for_all_signals(sprintf('(not (%s))', breach_formula), cfg.signalNames, cfg.domainBounds, domain_horizon, cfg.booleanSignalNames, cfg.discreteSignalNames, cfg.derivedSignalDefinitions);

    out_dir = resolve_repo_path(repo_root, cfg.outDir);
    if ~exist(out_dir, 'dir')
        mkdir(out_dir);
    end
    function_timer = tic;
    function_timer_cleanup = onCleanup(@() stats_append(out_dir, 'function', toc(function_timer), ''));
    export_dir = resolve_repo_path(repo_root, cfg.exportDir);

    fprintf('Formula CSV : %s\n', formula_csv);
    fprintf('Formula row : %d\n', cfg.row);
    fprintf('Oracle CSV  : %s\n', oracle_formula_csv);
    fprintf('Oracle row  : %d\n', cfg.oracleRow);
    fprintf('Work dir    : %s\n', out_dir);
    fprintf('Export dir  : %s\n', export_dir);
    fprintf('BUSTLE STL  : %s\n', formula_used);
    fprintf('Breach STL  : %s\n', breach_formula);
    fprintf('Gen SMT STL : %s\n', generation_formula);

    [sat_traces, sat_statuses] = solve_distinct_traces(generation_formula, cfg, false, cfg.satCount + cfg.satCandidateExtra, out_dir, 'satisfy');
    if cfg.skipEvaluation
        vio_traces = {};
        vio_statuses = {};
    else
        [vio_traces, vio_statuses] = solve_distinct_traces(violation_formula, cfg, false, cfg.vioCount + cfg.vioCandidateExtra, out_dir, 'violate');
    end

    if (~all(strcmp(sat_statuses, 'sat')) || (~cfg.skipEvaluation && ~all(strcmp(vio_statuses, 'sat')))) && cfg.allowFallback
        fprintf('Original formula could not generate all traces.\n');
        fprintf('Falling back to: %s\n', cfg.fallbackFormula);
        formula_used = cfg.fallbackFormula;
        breach_formula = bustle_to_breach(formula_used);
        generation_formula = augment_formula_for_all_signals(breach_formula, cfg.signalNames, cfg.domainBounds, domain_horizon, cfg.booleanSignalNames, cfg.discreteSignalNames, cfg.derivedSignalDefinitions);
        violation_formula = augment_formula_for_all_signals(sprintf('(not (%s))', breach_formula), cfg.signalNames, cfg.domainBounds, domain_horizon, cfg.booleanSignalNames, cfg.discreteSignalNames, cfg.derivedSignalDefinitions);
        [sat_traces, sat_statuses] = solve_distinct_traces(generation_formula, cfg, false, cfg.satCount + cfg.satCandidateExtra, out_dir, 'fallback_satisfy');
        if cfg.skipEvaluation
            vio_traces = {};
            vio_statuses = {};
        else
            [vio_traces, vio_statuses] = solve_distinct_traces(violation_formula, cfg, false, cfg.vioCount + cfg.vioCandidateExtra, out_dir, 'fallback_violate');
        end
        if cfg.rewriteCsvOnFallback
            rewrite_formula_csv(formula_csv, cfg.row, cfg.formulaColumn, formula_used);
        end
    end

    sat_ok = strcmp(sat_statuses, 'sat');
    vio_ok = strcmp(vio_statuses, 'sat');
    if (~all(sat_ok) || (~cfg.skipEvaluation && ~all(vio_ok))) && ~cfg.allowPartialGeneration
        error('Could not generate all requested traces.');
    end
    if ~all(sat_ok) || (~cfg.skipEvaluation && ~all(vio_ok))
        fprintf('Could not generate all requested traces; keeping partial SAT results (%d/%d)', ...
                sum(sat_ok), numel(sat_ok));
        if ~cfg.skipEvaluation
            fprintf(' and partial VIO results (%d/%d)', sum(vio_ok), numel(vio_ok));
        end
        fprintf('.\n');
    end
    sat_traces = sat_traces(sat_ok);
    sat_statuses = sat_statuses(sat_ok);
    if ~cfg.skipEvaluation
        vio_traces = vio_traces(vio_ok);
        vio_statuses = vio_statuses(vio_ok);
    end

    sample_times = cfg.sampleTimes;
    interp_timer = tic;
    sat_matrix = sample_trace_set(sat_traces, sample_times, cfg.signalPreference, cfg.signalNames);
    stats_append(out_dir, 'interp', toc(interp_timer), '');
    if cfg.skipEvaluation
        actual_sat_count = min(cfg.satCount, size(sat_matrix, 1));
        if actual_sat_count == 0
            error('Could not generate any satisfying trace.');
        end
        all_traces = sat_matrix(1:actual_sat_count, :);
        target_case_labels = ones(1, actual_sat_count);
        csv_timer = tic;
        write_dataset_files(out_dir, sample_times, all_traces, target_case_labels, formula, formula_used, breach_formula, cfg.signalNames);
        write_summary(out_dir, formula, formula_used, strjoin(sat_statuses, '|'), '', 'skipped', target_case_labels, target_case_labels);
        export_compact_dataset(out_dir, export_dir, target_case_labels, target_case_labels);
        cleanup_work_dir(out_dir, export_dir, cfg);
        stats_append(out_dir, 'csv', toc(csv_timer), '');
        fprintf('Done.\n');
        fprintf('Dataset CSV files written to: %s\n', export_dir);
        return;
    end
    vio_matrix = sample_trace_set(vio_traces, sample_times, cfg.signalPreference, cfg.signalNames);
    actual_sat_count = min(cfg.satCount, size(sat_matrix, 1));
    actual_vio_count = min(cfg.vioCount, size(vio_matrix, 1));
    if actual_sat_count + actual_vio_count == 0
        error('Could not generate any requested trace.');
    end
    candidate_traces = [sat_matrix; vio_matrix];
    target_case_labels = [ones(1, size(sat_matrix, 1)), -ones(1, size(vio_matrix, 1))];

    write_dataset_files(out_dir, sample_times, candidate_traces, target_case_labels, formula, formula_used, breach_formula, cfg.signalNames);
    candidate_case_labels = run_formula_eval(repo_root, out_dir, formula_used, cfg, 'case_formula_eval_candidates');
    selected_indexes = select_trace_subset(candidate_case_labels, size(sat_matrix, 1), actual_sat_count, size(vio_matrix, 1), actual_vio_count);
    if isempty(selected_indexes)
        if cfg.requireMoonlightCaseConsistency
            error('Could not find %d satisfying and %d violating traces that stay consistent with Moonlight.', actual_sat_count, actual_vio_count);
        end
        fprintf('Moonlight-consistent subset not found; using solver-generated SAT/VIO traces directly.\n');
        selected_indexes = [1:actual_sat_count, (size(sat_matrix, 1) + 1):(size(sat_matrix, 1) + actual_vio_count)];
    end

    all_traces = candidate_traces(selected_indexes, :);
    target_case_labels = [ones(1, actual_sat_count), -ones(1, actual_vio_count)];
    write_dataset_files(out_dir, sample_times, all_traces, target_case_labels, formula, formula_used, breach_formula, cfg.signalNames);

    case_labels = run_formula_eval(repo_root, out_dir, formula_used, cfg, 'case_formula_eval');
    moonlight_alignment = classify_alignment(case_labels, cfg.satCount, cfg.vioCount);
    oracle_labels = run_formula_eval(repo_root, out_dir, oracle_formula, cfg, 'oracle_formula_eval');

    write_summary(out_dir, formula, formula_used, strjoin(sat_statuses, '|'), strjoin(vio_statuses, '|'), moonlight_alignment, case_labels, oracle_labels);
    export_compact_dataset(out_dir, export_dir, case_labels, oracle_labels);
    cleanup_work_dir(out_dir, export_dir, cfg);

    fprintf('Done.\n');
    fprintf('Dataset CSV files written to: %s\n', export_dir);
end

function cfg = parse_options(varargin)
    parser = inputParser;
    addParameter(parser, 'datasetName', 'generic');
    addParameter(parser, 'formulaCsv', fullfile('dataset', 'case_formulas.csv'));
    addParameter(parser, 'formulaDialect', 'bustle');
    addParameter(parser, 'formulaColumn', 'formula');
    addParameter(parser, 'row', 1);
    addParameter(parser, 'oracleFormulaCsv', fullfile('dataset', 'oracle_formulas.csv'));
    addParameter(parser, 'oracleFormulaColumn', 'formula');
    addParameter(parser, 'oracleRow', 1);
    addParameter(parser, 'outDir', fullfile('dataset', '.case_generation_work'));
    addParameter(parser, 'exportDir', fullfile('dataset', 'generated'));
    addParameter(parser, 'sampleTimes', 0:5:300);
    addParameter(parser, 'signalNames', {'x1', 'x2'});
    addParameter(parser, 'booleanSignalNames', {});
    addParameter(parser, 'discreteSignalNames', {});
    addParameter(parser, 'derivedSignalDefinitions', {});
    addParameter(parser, 'domainBounds', {'x1', [1e-6, 0.999999], 'x2', [1e-6, 0.999999]});
    addParameter(parser, 'satCount', 5);
    addParameter(parser, 'vioCount', 5);
    addParameter(parser, 'satCandidateExtra', 5);
    addParameter(parser, 'vioCandidateExtra', 5);
    addParameter(parser, 'mT', 61);
    addParameter(parser, 'k', 8);
    addParameter(parser, 'signalPreference', 1); % 1 = piecewise linear, 0 = piecewise constant
    addParameter(parser, 'delta', 1e-6);
    addParameter(parser, 'blockingDelta', 0.02);
    addParameter(parser, 'allowFallback', false);
    addParameter(parser, 'allowPartialGeneration', true);
    addParameter(parser, 'rewriteCsvOnFallback', false);
    addParameter(parser, 'requireMoonlightCaseConsistency', true);
    addParameter(parser, 'keepWorkFiles', false);
    addParameter(parser, 'skipEvaluation', false);
    % Stop the distinct-trace loop at the first non-sat answer: 'off' (run all
    % `count` solves), 'unsat' (blocking clauses only shrink the model set, so
    % later solves cannot be sat; output-identical) or 'nonsat' (unsat and unknown).
    addParameter(parser, 'earlyExit', 'off');
    addParameter(parser, 'fallbackFormula', 'AND[EVENTUALLY I=[0, 1][x1 > 0.5],EVENTUALLY I=[0, 1][x2 > 0.5]]');
    java_bin = getenv('JAVA_BIN');
    if isempty(java_bin)
        java_bin = 'java';
    end
    addParameter(parser, 'javaBin', java_bin);
    parse(parser, varargin{:});
    cfg = parser.Results;
    if ~strcmp(cfg.formulaDialect, 'bustle') && ~strcmp(cfg.formulaDialect, 'breach')
        error('formulaDialect must be bustle or breach.');
    end
end

function generation_formula = augment_formula_for_all_signals(breach_formula, signal_names, domain_bounds, domain_horizon, boolean_signal_names, discrete_signal_names, derived_signal_definitions)
    generation_formula = breach_formula;
    for i = 1:numel(signal_names)
        signal_name = signal_names{i};
        if is_boolean_signal_name(signal_name, boolean_signal_names)
            point_guard = sprintf( ...
                '(((%s[t] >= 0) and (%s[t] <= 0)) or ((%s[t] >= 1) and (%s[t] <= 1)))', ...
                signal_name, signal_name, signal_name, signal_name);
        else
            bounds = lookup_signal_bounds(domain_bounds, signal_name);
            lower = bounds(1);
            upper = bounds(2);
            lower_text = format_decimal_literal(lower);
            upper_text = format_decimal_literal(upper);
            if is_boolean_signal_name(signal_name, discrete_signal_names)
                point_guard = sprintf('((%s[t] >= %s) and (%s[t] <= %s))', signal_name, lower_text, signal_name, upper_text);
            else
                point_guard = sprintf('((%s[t] > %s) and (%s[t] < %s))', signal_name, lower_text, signal_name, upper_text);
            end
        end
        guard = sprintf('(alw_[0,%s] (%s))', format_decimal_literal(domain_horizon), point_guard);
        generation_formula = sprintf('((%s) and (%s))', generation_formula, guard);
    end
    if mod(numel(derived_signal_definitions), 2) ~= 0
        error('derivedSignalDefinitions must contain pairs of {derived, definitionJson}.');
    end
    for i = 1:2:numel(derived_signal_definitions)
        derived = derived_signal_definitions{i};
        definition = jsondecode(derived_signal_definitions{i + 1});
        [equality, referenced] = derived_equality_formula(derived, definition);
        if ~any(strcmp(signal_names, derived)) || ~all(ismember(referenced, signal_names))
            error('Derived identity %s references an unknown signal.', derived);
        end
        relation_guard = sprintf('(alw_[0,%s] (%s))', format_decimal_literal(domain_horizon), equality);
        generation_formula = sprintf('((%s) and (%s))', generation_formula, relation_guard);
    end
end

function [equality, referenced] = derived_equality_formula(derived, definition)
    operation = definition.op;
    if strcmp(operation, 'subtract') || strcmp(operation, 'linear_difference')
        referenced = {definition.left, definition.right};
        rhs = sprintf('(%s[t] - %s[t])', definition.left, definition.right);
        equality = exact_real_equality(derived, rhs);
        return;
    end
    if strcmp(operation, 'linear_margin_subtract')
        referenced = {definition.left, definition.right, definition.subtrahend};
        rhs = sprintf('(%s[t] - %s[t] + %s[t])', definition.left, definition.right, definition.subtrahend);
        equality = exact_real_equality(derived, rhs);
        return;
    end
    if strcmp(operation, 'abs_difference')
        referenced = {definition.left, definition.right};
        raw = sprintf('(%s[t] - %s[t])', definition.left, definition.right);
        eq_positive = exact_real_equality(derived, raw);
        eq_negative = exact_real_equality(derived, sprintf('(-1 * %s)', raw));
        equality = sprintf('(((%s >= 0) and (%s)) or ((%s < 0) and (%s)))', ...
                           raw, eq_positive, raw, eq_negative);
        return;
    end
    if strcmp(operation, 'equals')
        % Boolean derived signal (guarded to {0,1} as a Boolean signal):
        % derived = 1 exactly when left == value.
        referenced = {definition.left};
        value = format_decimal_literal(str2double(definition.value));
        x = [definition.left '[t]'];
        equality = sprintf(['(((%s[t] >= 1) and (%s >= %s) and (%s <= %s)) or ' ...
                            '((%s[t] <= 0) and ((%s < %s) or (%s > %s))))'], ...
                           derived, x, value, x, value, derived, x, value, x, value);
        return;
    end
    if strcmp(operation, 'rss_lon_margin') || strcmp(operation, 'rss_lat_margin')
        referenced = {definition.position_a, definition.position_b, definition.size, ...
                      definition.velocity_1, definition.velocity_2};
        v1 = [definition.velocity_1 '[t]'];
        v2 = [definition.velocity_2 '[t]'];
        if strcmp(operation, 'rss_lon_margin')
            raw = sprintf('((%s * 0.6) + 0.9 + (((%s + 3) * (%s + 3)) / 12) - ((%s * %s) / 16))', ...
                          v1, v1, v1, v2, v2);
        else
            raw = sprintf('(((%s - %s) * 0.6) + 0.54 + ((((%s + 0.9) * (%s + 0.9)) + ((%s - 0.9) * (%s - 0.9))) / 3))', ...
                          v1, v2, v1, v1, v2, v2);
        end
        gap_without_rss = sprintf('(%s[t] - %s[t] - %s[t])', ...
                                  definition.position_a, definition.position_b, definition.size);
        rhs_positive = sprintf('(%s - %s)', gap_without_rss, raw);
        eq_positive = exact_real_equality(derived, rhs_positive);
        eq_zero = exact_real_equality(derived, gap_without_rss);
        equality = sprintf('(((%s >= 0) and (%s)) or ((%s < 0) and (%s)))', ...
                           raw, eq_positive, raw, eq_zero);
        return;
    end
    error('Unsupported derived signal operation: %s', operation);
end

function equality = exact_real_equality(derived, rhs)
    equality = sprintf('((%s[t] >= %s) and (%s[t] <= %s))', derived, rhs, derived, rhs);
end

function tf = is_boolean_signal_name(signal_name, boolean_signal_names)
    tf = false;
    for i = 1:numel(boolean_signal_names)
        if strcmp(signal_name, boolean_signal_names{i})
            tf = true;
            return;
        end
    end
end

function present = extract_formula_vars(formula, signal_names)
    present = {};
    for i = 1:numel(signal_names)
        signal_name = signal_names{i};
        pattern = [regexptranslate('escape', signal_name) '\[t\]'];
        if ~isempty(regexp(formula, pattern, 'once'))
            present{end + 1} = signal_name; %#ok<AGROW>
        end
    end
end

function bounds = lookup_signal_bounds(domain_bounds, signal_name)
    bounds = [1e-6, 0.999999];
    for i = 1:2:numel(domain_bounds)
        if strcmp(domain_bounds{i}, signal_name)
            bounds = domain_bounds{i + 1};
            return;
        end
    end
end

function text = format_decimal_literal(value)
    text = sprintf('%.15f', value);
    text = regexprep(text, '0+$', '');
    text = regexprep(text, '\.$', '');
    if isempty(text)
        text = '0';
    end
end

function repo_root = resolve_repo_root()
    script_dir = fileparts(mfilename('fullpath'));
    repo_root = char(java.io.File(fullfile(script_dir, '..', '..')).getCanonicalPath());
end

function setup_paths(repo_root)
    addpath(genpath(fullfile(repo_root, 'stl-trace-generation', 'breach')));
    addpath(fullfile(repo_root, 'stl-trace-generation', 'STL_TO_SMT'));
end

function formula = read_formula_from_csv(csv_path, row_idx, column_name)
    % Do not let MATLAB infer whitespace as a delimiter for atomic formulas
    % such as "NOT[x2 > 0]"; the formula files are comma-separated CSVs.
    table_data = readtable( ...
        csv_path, ...
        'TextType', 'string', ...
        'Delimiter', ',', ...
        'VariableNamingRule', 'preserve');
    if height(table_data) > 0
        if ~ismember(column_name, table_data.Properties.VariableNames)
            error('Column "%s" not found in %s', column_name, csv_path);
        end
        if row_idx < 1 || row_idx > height(table_data)
            error('Row %d is outside CSV range 1..%d', row_idx, height(table_data));
        end
        formula = char(table_data{row_idx, column_name});
        return;
    end

    % Fallback: some single-column CSVs occasionally get parsed as 0-row tables
    % by readtable; read the raw text instead of failing immediately.
    raw = fileread(csv_path);
    lines = regexp(raw, '\r\n|\n|\r', 'split');
    lines = lines(~cellfun(@isempty, lines));
    if numel(lines) < 2
        error('Row %d is outside CSV range 1..0', row_idx);
    end
    if row_idx ~= 1
        error('Row %d is outside CSV range 1..1', row_idx);
    end
    header = strtrim(lines{1});
    if ~strcmp(header, column_name)
        error('Column "%s" not found in %s', column_name, csv_path);
    end
    value = strtrim(lines{2});
    if numel(value) >= 2 && value(1) == '"' && value(end) == '"'
        value = value(2:end-1);
    end
    formula = value;
end

function rewrite_formula_csv(csv_path, row_idx, column_name, formula)
    table_data = readtable(csv_path, 'TextType', 'string');
    table_data{row_idx, column_name} = string(formula);
    writetable(table_data, csv_path);
end

function [traces, statuses] = solve_distinct_traces(breach_formula, cfg, check_negation, count, out_dir, stem)
    traces = cell(1, count);
    statuses = cell(1, count);
    phi = STL_Formula(['generated_case_' stem], breach_formula);
    trace_dir = fullfile(out_dir, [stem '_distinct']);
    if ~exist(trace_dir, 'dir')
        mkdir(trace_dir);
    end
    old_dir = pwd;
    cleanup = onCleanup(@() cd(old_dir));
    cd(trace_dir);

    global ATG_Z3_LAST_SECONDS
    ATG_Z3_LAST_SECONDS = 0;
    solve_timer = tic;
    [signames, X, t] = smt_encode_and_check(phi, cfg.mT, cfg.k, cfg.signalPreference, cfg.delta, check_negation);
    solve_total = toc(solve_timer);
    copyfile('model.txt', fullfile(out_dir, sprintf('%s_%02d_model.txt', stem, 1)));
    traces{1} = make_trace(signames, X, t, cfg.signalNames);
    statuses{1} = read_solver_status(fullfile(out_dir, sprintf('%s_%02d_model.txt', stem, 1)));
    stats_append(out_dir, 'z3', ATG_Z3_LAST_SECONDS, statuses{1});
    stats_append(out_dir, 'encode', max(solve_total - ATG_Z3_LAST_SECONDS, 0), '');
    if strcmp(statuses{1}, 'sat') && ~isempty(X)
        write_solver_trace_csv(fullfile(out_dir, sprintf('%s_%02d_solver_trace.csv', stem, 1)), signames, X, t);
    end

    generated = 1;
    stop_reason = 'count_reached';
    if early_exit_triggered(cfg.earlyExit, statuses{1})
        stop_reason = ['early_exit_' statuses{1}];
    end
    for i = 2:count
        if ~strcmp(stop_reason, 'count_reached')
            break;
        end
        ATG_Z3_LAST_SECONDS = 0;
        solve_timer = tic;
        [signames, X, t] = smt_add_blocking_and_check( ...
            phi, 'model.txt', 'stl_translation.smt2', ...
            'RealValuedBlocking', true, ...
            'BooleanValuedBlocking', true, ...
            'Delta', cfg.blockingDelta);
        solve_total = toc(solve_timer);
        copyfile('model.txt', fullfile(out_dir, sprintf('%s_%02d_model.txt', stem, i)));
        traces{i} = make_trace(signames, X, t, cfg.signalNames);
        statuses{i} = read_solver_status(fullfile(out_dir, sprintf('%s_%02d_model.txt', stem, i)));
        stats_append(out_dir, 'z3', ATG_Z3_LAST_SECONDS, statuses{i});
        stats_append(out_dir, 'encode', max(solve_total - ATG_Z3_LAST_SECONDS, 0), '');
        if strcmp(statuses{i}, 'sat') && ~isempty(X)
            write_solver_trace_csv(fullfile(out_dir, sprintf('%s_%02d_solver_trace.csv', stem, i)), signames, X, t);
        end
        generated = i;
        if early_exit_triggered(cfg.earlyExit, statuses{i})
            stop_reason = ['early_exit_' statuses{i}];
        end
    end
    % Pad skipped slots with the terminating answer so callers that test
    % all(strcmp(statuses, 'sat')) see exactly what the full loop produced.
    for i = (generated + 1):count
        traces{i} = traces{generated};
        statuses{i} = statuses{generated};
    end
    stats_append(out_dir, 'distinct', generated, sprintf('%s|requested=%d|%s', stem, count, stop_reason));
end

function tf = early_exit_triggered(mode, status)
    tf = (strcmp(mode, 'unsat') && strcmp(status, 'unsat')) || ...
         (strcmp(mode, 'nonsat') && ~strcmp(status, 'sat'));
end

function stats_append(out_dir, key, seconds, status)
    % One line per measurement; the Python driver sums them into run_record.json.
    fid = fopen(fullfile(out_dir, 'trace_gen_stats.csv'), 'a');
    if fid == -1
        return;
    end
    fprintf(fid, '%s,%.6f,%s\n', key, seconds, status);
    fclose(fid);
end

function trace = make_trace(signames, X, t, signal_names)
    values = struct();
    for i = 1:numel(signal_names)
        signal_name = signal_names{i};
        values.(signal_name) = [];
        if ~isempty(X)
            signal_index = find(strcmp(signames, signal_name), 1);
            if ~isempty(signal_index)
                values.(signal_name) = X(signal_index, :);
            end
        end
    end
    trace = struct('signames', {signames}, 'X', X, 't', t, 'values', values);
end

function status = read_solver_status(model_file)
    fid = fopen(model_file, 'r');
    if fid == -1
        error('Could not open solver model file: %s', model_file);
    end
    first_line = fgetl(fid);
    fclose(fid);
    if ~ischar(first_line)
        error('Solver model file is empty. Check that z3 is available on PATH: %s', model_file);
    end
    status = strtrim(first_line);
end

function sampled = sample_signal(t, x, sample_times, signal_preference)
    if isempty(t) || isempty(x)
        error('Cannot sample an empty solver trace.');
    end
    [t_unique, idx] = unique(t, 'stable');
    x_unique = x(idx);
    query_times = remap_sample_times(sample_times, t_unique);
    if numel(t_unique) == 1
        sampled = repmat(x_unique(1), size(sample_times));
    elseif signal_preference == 1
        sampled = interp1(t_unique, x_unique, query_times, 'linear');
    else
        sampled = interp1(t_unique, x_unique, query_times, 'previous');
    end
end

function query_times = remap_sample_times(sample_times, t_unique)
    sample_min = min(sample_times);
    sample_max = max(sample_times);
    trace_min = min(t_unique);
    trace_max = max(t_unique);
    if sample_max > trace_max + 1e-9 && sample_max > sample_min
        normalized = (sample_times - sample_min) / (sample_max - sample_min);
        query_times = trace_min + normalized * (trace_max - trace_min);
    else
        query_times = min(max(sample_times, trace_min), trace_max);
    end
end

function sampled_matrix = sample_trace_set(traces, sample_times, signal_preference, signal_names)
    num_traces = numel(traces);
    num_signals = numel(signal_names);
    num_times = numel(sample_times);
    sampled_matrix = zeros(num_traces, num_signals * num_times);
    for i = 1:num_traces
        for j = 1:num_signals
            signal_name = signal_names{j};
            signal_values = traces{i}.values.(signal_name);
            if isempty(signal_values)
                error('Trace %d has no %s signal.', i, signal_name);
            end
            sampled = sample_signal(traces{i}.t, signal_values, sample_times, signal_preference);
            sampled_matrix(i, j:num_signals:end) = sampled;
        end
    end
end

function write_dataset_files(out_dir, sample_times, traces, labels, original_formula, formula_used, breach_formula, signal_names)
    write_text(fullfile(out_dir, 'time.csv'), join_numbers(sample_times));
    write_text(fullfile(out_dir, 'times.csv'), join_numbers(sample_times));
    write_text(fullfile(out_dir, 'label.csv'), join_labels(labels));
    write_text(fullfile(out_dir, 'labels.csv'), join_labels(labels));
    write_text(fullfile(out_dir, 'formula_original.txt'), original_formula);
    write_text(fullfile(out_dir, 'formula_used.txt'), formula_used);
    write_text(fullfile(out_dir, 'formula_breach.txt'), breach_formula);

    fid = fopen(fullfile(out_dir, 'data.csv'), 'w');
    fprintf(fid, '%s\n', strjoin(signal_names, ','));
    for i = 1:size(traces, 1)
        fprintf(fid, '%s\n', join_numbers(traces(i, :)));
    end
    fclose(fid);

    fid = fopen(fullfile(out_dir, 'source_formula.csv'), 'w');
    fprintf(fid, 'formula\n"%s"\n', strrep(formula_used, '"', '""'));
    fclose(fid);
end

function labels = run_formula_eval(repo_root, out_dir, formula_used, cfg, stem)
    output_csv = fullfile(out_dir, [stem '.csv']);
    classpath = 'libs/JGEA.jar:libs/moonlight.jar:libs/jblas-1.2.4.jar:target/classes';
    data_path_arg = cfg.outDir;
    if is_absolute_path(data_path_arg)
        data_path_arg = out_dir;
    end
    arch_prefix = '';
    if ismac
        arch_prefix = 'arch -x86_64 ';
    end
    cmd = [
        'cd ' shell_quote(repo_root) ' && ' ...
        arch_prefix shell_quote(cfg.javaBin) ' ' ...
        '-cp ' shell_quote(classpath) ' ' ...
        'it.units.malelab.learningstl.EvaluateFormulaOnDataset ' ...
        ['dataset=' cfg.datasetName ' '] ...
        ['dataPath=' shell_quote(data_path_arg) ' '] ...
        'strictLengthCheck=false ' ...
        ['formula=' shell_quote(formula_used) ' '] ...
        ['output=' shell_quote(output_csv)]
    ];
    [status, output] = system(cmd);
    if status ~= 0
        error('Moonlight evaluation failed:\n%s', output);
    end
    labels = labels_from_moonlight_csv(output_csv);
end

function alignment = classify_alignment(case_labels, sat_count, vio_count)
    expected = [ones(1, sat_count), -ones(1, vio_count)];
    if numel(case_labels) == numel(expected) && all(case_labels(:)' == expected)
        alignment = 'matched';
    else
        alignment = 'mismatch';
    end
end

function selected_indexes = select_trace_subset(candidate_case_labels, sat_candidate_count, sat_count, vio_candidate_count, vio_count)
    sat_indexes = find(candidate_case_labels(1:sat_candidate_count) == 1);
    vio_block = candidate_case_labels(sat_candidate_count + 1:sat_candidate_count + vio_candidate_count);
    vio_indexes = find(vio_block == -1) + sat_candidate_count;
    if numel(sat_indexes) < sat_count || numel(vio_indexes) < vio_count
        selected_indexes = [];
        return;
    end
    selected_indexes = [sat_indexes(1:sat_count), vio_indexes(1:vio_count)];
end

function export_compact_dataset(out_dir, export_dir, case_labels, oracle_labels)
    if ~exist(export_dir, 'dir')
        mkdir(export_dir);
    end
    copyfile(fullfile(out_dir, 'time.csv'), fullfile(export_dir, 'time.csv'));
    copyfile(fullfile(out_dir, 'data.csv'), fullfile(export_dir, 'data.csv'));
    write_text(fullfile(export_dir, 'case_label.csv'), join_labels(case_labels));
    write_text(fullfile(export_dir, 'oracle_label.csv'), join_labels(oracle_labels));
end

function cleanup_work_dir(out_dir, export_dir, cfg)
    if cfg.keepWorkFiles
        return;
    end
    out_canonical = char(java.io.File(out_dir).getCanonicalPath());
    export_canonical = char(java.io.File(export_dir).getCanonicalPath());
    if ~strcmp(out_canonical, export_canonical) && contains(out_canonical, [filesep '.case_generation_work'])
        rmdir(out_dir, 's');
    end
end

function path = resolve_repo_path(repo_root, path_text)
    path_text = char(path_text);
    if isempty(path_text)
        path = path_text;
        return;
    end
    if is_absolute_path(path_text)
        path = path_text;
    else
        path = fullfile(repo_root, path_text);
    end
end

function tf = is_absolute_path(path_text)
    path_text = char(path_text);
    tf = startsWith(path_text, filesep) || ~isempty(regexp(path_text, '^[A-Za-z]:[\\/]', 'once'));
end

function labels = labels_from_moonlight_csv(path)
    fid = fopen(path, 'r');
    header = strsplit(strtrim(fgetl(fid)), ',');
    status_idx = find(strcmp(header, 'status'), 1);
    labels = [];
    line = fgetl(fid);
    while ischar(line)
        if ~startsWith(line, '#') && ~isempty(strtrim(line))
            fields = strsplit(line, ',');
            status = fields{status_idx};
            if strcmp(status, 'SAT')
                labels(end + 1) = 1; %#ok<AGROW>
            else
                labels(end + 1) = -1; %#ok<AGROW>
            end
        end
        line = fgetl(fid);
    end
    fclose(fid);
end

function write_summary(out_dir, original_formula, formula_used, sat_status, vio_status, moonlight_alignment, case_labels, oracle_labels)
    fid = fopen(fullfile(out_dir, 'summary.txt'), 'w');
    fprintf(fid, 'original_formula=%s\n', original_formula);
    fprintf(fid, 'formula_used=%s\n', formula_used);
    fprintf(fid, 'satisfying_query=%s\n', sat_status);
    fprintf(fid, 'violating_query=%s\n', vio_status);
    fprintf(fid, 'moonlight_alignment=%s\n', moonlight_alignment);
    fprintf(fid, 'case_labels=%s\n', join_labels(case_labels));
    fprintf(fid, 'oracle_labels=%s\n', join_labels(oracle_labels));
    fclose(fid);
end

function write_solver_trace_csv(filename, signames, X, t)
    fid = fopen(filename, 'w');
    fprintf(fid, 'time');
    for i = 1:numel(signames)
        fprintf(fid, ',%s', signames{i});
    end
    fprintf(fid, '\n');
    for j = 1:numel(t)
        fprintf(fid, '%.17g', t(j));
        for i = 1:numel(signames)
            fprintf(fid, ',%.17g', X(i, j));
        end
        fprintf(fid, '\n');
    end
    fclose(fid);
end

function breach = bustle_to_breach(formula)
    formula = strtrim(formula);
    if startsWith(formula, 'AND[')
        args = parse_call_args(formula, 'AND');
        breach = fold_binary(args, 'and');
    elseif startsWith(formula, 'OR[')
        args = parse_call_args(formula, 'OR');
        breach = fold_binary(args, 'or');
    elseif startsWith(formula, 'IMPLIES[')
        % Breach has no implication, so it is expanded here - at the boundary
        % where the formula leaves BUSTLE for good, not in the round trip.
        args = parse_call_args(formula, 'IMPLIES');
        require_arity(args, 2, 'IMPLIES');
        breach = sprintf('((not (%s)) or (%s))', bustle_to_breach(args{1}), bustle_to_breach(args{2}));
    elseif startsWith(formula, 'NOT[')
        args = parse_call_args(formula, 'NOT');
        require_arity(args, 1, 'NOT');
        breach = ['not (' bustle_to_breach(args{1}) ')'];
    elseif startsWith(formula, 'EVENTUALLY')
        [a, b, args] = parse_temporal_call(formula, 'EVENTUALLY');
        require_arity(args, 1, 'EVENTUALLY');
        breach = sprintf('ev_[%s,%s] (%s)', a, b, bustle_to_breach(args{1}));
    elseif startsWith(formula, 'GLOBALLY')
        [a, b, args] = parse_temporal_call(formula, 'GLOBALLY');
        require_arity(args, 1, 'GLOBALLY');
        breach = sprintf('alw_[%s,%s] (%s)', a, b, bustle_to_breach(args{1}));
    elseif startsWith(formula, 'UNTIL')
        [a, b, args] = parse_temporal_call(formula, 'UNTIL');
        require_arity(args, 2, 'UNTIL');
        breach = sprintf('(%s) until_[%s,%s] (%s)', bustle_to_breach(args{1}), a, b, bustle_to_breach(args{2}));
    else
        breach = atom_to_breach(formula);
    end
end

function breach = atom_to_breach(atom)
    tokens = regexp(strtrim(atom), '^([A-Za-z_]\w*)\s*(<=|>=|==|=|<|>)\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)$', 'tokens', 'once');
    if isempty(tokens)
        error('Cannot parse atom: %s', atom);
    end
    op = tokens{2};
    if strcmp(op, '=')
        op = '==';
    end
    if strcmp(op, '==')
        breach = sprintf('((%s[t] >= %s) and (%s[t] <= %s))', tokens{1}, tokens{3}, tokens{1}, tokens{3});
    else
        breach = sprintf('%s[t] %s %s', tokens{1}, op, tokens{3});
    end
end

function args = parse_call_args(formula, name)
    prefix = [name '['];
    inner = formula(numel(prefix) + 1:end - 1);
    args = split_top_level(inner);
end

function [a, b, args] = parse_temporal_call(formula, name)
    prefix = [name ' I=['];
    if ~startsWith(formula, prefix)
        error('Expected temporal call %s in %s', name, formula);
    end
    pos = numel(prefix) + 1;
    interval_end = find_matching(formula, pos - 1, '[', ']');
    bounds = split_top_level(formula(pos:interval_end - 1));
    require_arity(bounds, 2, [name ' interval']);
    a = strtrim(bounds{1});
    b = strtrim(bounds{2});
    args_start = interval_end + 1;
    while args_start <= numel(formula) && isspace(formula(args_start))
        args_start = args_start + 1;
    end
    if args_start > numel(formula) || formula(args_start) ~= '['
        error('Expected argument list in %s', formula);
    end
    args_end = find_matching(formula, args_start, '[', ']');
    args = split_top_level(formula(args_start + 1:args_end - 1));
end

function args = split_top_level(text)
    args = {};
    start_idx = 1;
    depth_square = 0;
    depth_round = 0;
    for i = 1:numel(text)
        ch = text(i);
        if ch == '['
            depth_square = depth_square + 1;
        elseif ch == ']'
            depth_square = depth_square - 1;
        elseif ch == '('
            depth_round = depth_round + 1;
        elseif ch == ')'
            depth_round = depth_round - 1;
        elseif ch == ',' && depth_square == 0 && depth_round == 0
            args{end + 1} = strtrim(text(start_idx:i - 1)); %#ok<AGROW>
            start_idx = i + 1;
        end
    end
    tail = strtrim(text(start_idx:end));
    if ~isempty(tail)
        args{end + 1} = tail; %#ok<AGROW>
    end
end

function idx = find_matching(text, start_idx, open_char, close_char)
    depth = 0;
    for idx = start_idx:numel(text)
        if text(idx) == open_char
            depth = depth + 1;
        elseif text(idx) == close_char
            depth = depth - 1;
            if depth == 0
                return;
            end
        end
    end
    error('Could not find matching %s for %s in %s', close_char, open_char, text);
end

function require_arity(args, expected, name)
    if numel(args) ~= expected
        error('%s expects %d arguments, got %d', name, expected, numel(args));
    end
end

function out = fold_binary(args, op)
    if isempty(args)
        error('%s requires at least one argument.', upper(op));
    end
    out = ['(' bustle_to_breach(args{1}) ')'];
    for i = 2:numel(args)
        out = [out ' ' op ' (' bustle_to_breach(args{i}) ')'];
    end
end

function text = join_numbers(values)
    parts = arrayfun(@(x) sprintf('%.17g', x), values, 'UniformOutput', false);
    text = strjoin(parts, ',');
end

function text = join_labels(labels)
    parts = arrayfun(@(x) sprintf('%d', round(x)), labels, 'UniformOutput', false);
    text = strjoin(parts, ',');
end

function write_text(path, text)
    fid = fopen(path, 'w');
    fprintf(fid, '%s', text);
    fclose(fid);
end

function quoted = shell_quote(text)
    quoted = ['''' strrep(char(text), '''', '''"''"''') ''''];
end
