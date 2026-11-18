% Provenance: STL Repair project-specific integration component.
%
% Long-lived MATLAB worker for the Python driver (--atg-persistent-matlab).
% Starting MATLAB costs seconds per `matlab -batch`; this loop pays that once
% and then runs the same run_atg.m job scripts the per-call launcher would.
%
% Protocol (all files live in job_dir):
%   ready.flag          written once the server is polling
%   job_<n>.run         Python's atomic "go": its one line is the job script path
%   job_<n>.out         captured command-window output of the job
%   job_<n>.done        "0" (job finished) or "1" (job raised an error)
%   quit                created by Python to stop the server
%
% Job scripts signal failure with error(); they must not call exit().

function atg_server(job_dir)
    job_dir = char(job_dir);
    fid = fopen(fullfile(job_dir, 'ready.flag'), 'w');
    fclose(fid);
    while ~exist(fullfile(job_dir, 'quit'), 'file')
        jobs = dir(fullfile(job_dir, 'job_*.run'));
        if isempty(jobs)
            pause(0.05);
            continue;
        end
        [~, order] = sort({jobs.name});
        jobs = jobs(order);
        run_file = fullfile(job_dir, jobs(1).name);
        [~, stem] = fileparts(jobs(1).name);
        script_path = strtrim(fileread(run_file));
        delete(run_file);

        status = '0';
        out_path = fullfile(job_dir, [stem '.out']);
        if exist(out_path, 'file')
            delete(out_path);
        end
        % diary keeps what the job printed even when it ends in an error.
        diary(out_path);
        diary on;
        try
            run(script_path);
        catch ME
            status = '1';
            disp(getReport(ME, 'extended', 'hyperlinks', 'off'));
        end
        diary off;
        % .done last: Python treats it as "output is complete".
        done_fid = fopen(fullfile(job_dir, [stem '.done']), 'w');
        fprintf(done_fid, '%s', status);
        fclose(done_fid);
    end
end
