% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function new_str = remove_redundant_paranthesis(str)
    if isequal(str, -1) || isempty(str)
        error('remove_redundant_paranthesis:EmptyInput', ...
            'Input string is empty; prefix conversion likely failed.');
    end
    if isstring(str)
        str = char(str);
    elseif ~ischar(str)
        str = char(string(str));
    end
    str = regexprep(str, '\((\s*\w*\s*)\)', ' $1 ');
    while 1
        str1 = regexprep(str, '(\()\s+(\()', '$1$2'); 
        if strcmp(str1,str)
            break;
        else
            str = str1;
        end
    end
    %new_str = regexprep(str, '\(\s*\(([^\(\)]*)\)\s*\)', ' ($1) ');
    opar = strfind(str, '(');
    cpar = strfind(str, ')');
    j = 0;
    so = size(opar);
    b = 0;
    for i=1:so(2)-1
        if opar(i+1) == opar(i) + 1
            b = 1;
            %remove the ( corr opar(i+1) and the ) corresp to cpar(so(2)- i)
            j = j + 1;
            A{j,1} = i;
            A{j,2} = so(2)- i + 1;
        end
    end
    
    if b == 1
    sA = size(A);
    for i = 1:sA(1)
        str = regexprep(str,')',' ',A{i,2});
        str = regexprep(str,'(',' ',A{sA(1)-i+1, 1});
    end
    end
    new_str = str;
