% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function output = sub_param(input, main_formula) 
    defparams = get_params(main_formula);
    paramnames = fieldnames(defparams);
    output = input;
    for i = 1:numel(paramnames)
        if not(strcmp(paramnames{i,1}, 'dt'))
            val = num2str(eval(strcat('defparams.', paramnames{i,1})));
            output = regexprep(output, strcat('(\W*)',paramnames{i,1},'(\W*)'), strcat('$1',val,'$2'));
        end
    end  
