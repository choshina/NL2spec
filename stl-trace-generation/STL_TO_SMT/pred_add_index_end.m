% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function new_str = pred_add_index_end(str, j)
    new_str = regexprep(str, strcat('(\W*)(\w*[a-zA-Z]\w*)(\W*)'), strcat('$1','$2_', num2str(j),'_end','$3'));
