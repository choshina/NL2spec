% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function new_str = add_zero_decimals(str)
    % also replacing .4 by 0.4
    new_str = regexprep(str, '\s\.([0-9])', ' 0.$1');
