function params = get_params(phi)
disp(phi)
if isfield(phi.params,'default_params')
    params = phi.params.default_params;
else
    params = struct;
end
end
