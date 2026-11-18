% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function [l, h] = param_int(formula,main_formula)
    defparams = get_params(main_formula);
    paramnames = fieldnames(defparams);
    for i = 1:numel(paramnames)
        param_assignment = strcat(paramnames{i,1}, ' = ', 'defparams.', paramnames{i,1},';');
        eval(param_assignment);
    end  
    interval_str = get_interval(formula);
    comp_str = '\[(?<lowint>.*),\s*(?<highint>.*)\]';
    interval_names = regexp(interval_str, comp_str, 'names');
    l = eval(interval_names.lowint);
    h = eval(interval_names.highint);
