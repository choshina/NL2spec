% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function T = STL_Parse_Table(formula)
Current = 1;
i = 1;
T{1,1} = formula_to_char(formula);

main_formula = formula;

while i <= Current
    stlstr = T{i,1};
    if not (strcmp(stlstr, 'aux'))
        formula = STL_Formula('name', stlstr);
        T{i,2} = get_type(formula);
        [phi,phi1,phi2] = get_parsetreechildren(formula);
        if isempty(phi) & not (isempty(phi1))
            % Binary operator
            T{i,3} = Current+1;
            if strcmp(T{i,2},'until')
                [T{i,5}, T{i,6}] = param_int(formula,main_formula);
                T{Current+1,1} = 'aux';
                T{Current+1,2} = 'aux';
                T{Current+2,1} = 'aux';
                T{Current+2,2} = 'aux';
                T{Current+3,1} = 'aux';
                T{Current+3,2} = 'aux';
                T{Current+4,1} = 'aux';
                T{Current+4,2} = 'aux';
                T{Current+5,1} = 'aux';
                T{Current+5,2} = 'aux';
                T{Current+6,1} = 'aux';
                T{Current+6,2} = 'aux';
                T{Current+7,1} = 'aux';
                T{Current+7,2} = 'aux';
                Current = Current + 7;
            else
                T{i,5} = -1;
                T{i,6} = -1;
            end
            T{i,4} = Current+2;
            T{Current+1,1} = formula_to_char(phi1);
            T{Current+2,1} = formula_to_char(phi2);
            Current = Current+2;
        elseif not (isempty(phi))
            % Unary operator
            T{i,3} = Current+1;
            T{i,4} = 0;
            if strcmp(T{i,2},'always')|strcmp(T{i,2},'eventually')
                [T{i,5}, T{i,6}] = param_int(formula, main_formula);
                if strcmp(T{i,2},'eventually')
                    T{Current+1,1} = 'aux';
                    T{Current+1,2} = 'aux';
                    T{Current+2,1} = 'aux';
                    T{Current+2,2} = 'aux';
                    Current = Current + 2;
                end
            else
                T{i,5} = -1;
                T{i,6} = -1;
            end
            T{Current+1,1} = formula_to_char(phi);
            Current = Current+1;
        else
             [T{i,3}, T{i,4}] = dt_shifted_signals(T{i,1});
            str = sub_param(T{i,1},main_formula);
            str = strip_redundant_outer_parens(str);
            str = add_zero_decimals(str);
            str = convert_brackets_to_underscores(str);
            str = replace_underscore(str);
            [pathstr,~,~] = fileparts(mfilename('fullpath'));
            infix2prefix_dir = [pathstr filesep 'infix2prefix'];
            % The SMT caller has already changed into its per-job trace
            % directory.  Keep converter input/output there as well: placing
            % these fixed names in the shared source directory lets two
            % independent MATLAB/ATG processes overwrite each other's atom.
            converter_work_dir = pwd;
            infix_path = [converter_work_dir filesep 'infix.txt'];
            prefix_path = [converter_work_dir filesep 'prefix.txt'];
            fid = fopen(infix_path,'w');
            fprintf(fid, str);    
            fclose(fid);
            java_cmd = sprintf('java -cp "%s" Infix2Prefix "%s" > "%s"', ...
                strrep(infix2prefix_dir, '"', '\"'), ...
                strrep(infix_path, '"', '\"'), ...
                strrep(prefix_path, '"', '\"'));
            status = system(java_cmd);
            if status ~= 0
                error('STL_Parse_Table:PrefixConversionFailed', ...
                    'Infix2Prefix exited with status %d for atom: %s', status, T{i,1});
            end
            fid = fopen(prefix_path, 'r');
            str = fgets(fid);
            if isequal(str, -1) || isempty(str)
                fclose(fid);
                error('STL_Parse_Table:PrefixConversionFailed', ...
                    'Infix2Prefix produced empty output for atom: %s', T{i,1});
            end
            str = remove_redundant_paranthesis(str);
            fclose(fid);
            T{i,1} = revert_underscore(str);
        end
    end
    i = i+1;
end
end

function out = formula_to_char(phi)
    if isstring(phi)
        out = char(phi);
        return;
    end
    if ischar(phi)
        out = phi;
        return;
    end
    if isnumeric(phi) || islogical(phi)
        out = char(string(phi));
        return;
    end
    try
        out = char(string(disp(phi)));
    catch
        out = evalc('disp(phi)');
        out = strtrim(out);
    end
end

function out = strip_redundant_outer_parens(str)
    out = strtrim(str);
    while numel(out) >= 2 && out(1) == '(' && out(end) == ')'
        depth = 0;
        wraps_entire = true;
        for idx = 1:numel(out)
            if out(idx) == '('
                depth = depth + 1;
            elseif out(idx) == ')'
                depth = depth - 1;
                if depth == 0 && idx < numel(out)
                    wraps_entire = false;
                    break;
                end
            end
        end
        if ~wraps_entire || depth ~= 0
            break;
        end
        out = strtrim(out(2:end-1));
    end
end
