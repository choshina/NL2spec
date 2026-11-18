% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function smt_initialize_signals(stlobject, T, k, signal_pref, delta, fid)
    fprintf(fid, '\n\n ; INITIALIZE SIGNALS \n');
    signames = STL_ExtractSignals(stlobject);
    defparams = get_params(stlobject);
        
    for i = 1:length(signames)
        for j = 1:k
            fprintf(fid, '(declare-fun %s_t_%d () Real)\n', signames{1,i}, j);
            if signal_pref == 1 & j > 1
                fprintf(fid, '(declare-fun %s_t_%d_end () Real)\n', signames{1,i}, j-1);
                fprintf(fid, '(assert (and (<= (- %s_t_%d_end %s_t_%d) %f) (>= (- %s_t_%d_end %s_t_%d) %f)))\n', signames{1,i}, j-1, signames{1,i}, j, delta, signames{1,i}, j-1, signames{1,i}, j, -delta);
            end
        end
        
        if plus_signal(signames{1,i}, T)
            dt = defparams.dt;
            for j = 1:k
            %fprintf(fid, '\n\n ; INITIALIZE POSITIVE TIME SHIFTED SIGNALS \n');
                fprintf(fid, '(declare-fun %s_t_p_dt_%d () Real)\n', signames{1,i}, j);
                if signal_pref == 1 & j > 1
                    fprintf(fid, '(declare-fun %s_t_p_dt_%d_end () Real)\n', signames{1,i}, j-1);       
                    fprintf(fid, '(assert (and (<= (- %s_t_p_dt_%d_end %s_t_p_dt_%d) %f) (>= (- %s_t_p_dt_%d_end %s_t_p_dt_%d) %f)))\n', signames{1,i}, j-1, signames{1,i}, j, delta, signames{1,i}, j-1, signames{1,i}, j, -delta);
                end
            end
            if signal_pref == 0
                check_t_plus_dt_constant(signames{1,i},k,dt,fid);
            else
                check_t_plus_dt_linear(signames{1,i},k,dt,fid);
            end
        end
        
        if minus_signal(signames{1,i}, T)
            dt = defparams.dt;
            for j = 1:k
            %fprintf(fid, '\n\n ; INITIALIZE NEGATIVE TIME SHIFTED SIGNALS \n');
                fprintf(fid, '(declare-fun %s_t_m_dt_%d () Real)\n', signames{1,i}, j);
                if signal_pref == 1 & j > 1
                    fprintf(fid, '(declare-fun %s_t_m_dt_%d_end () Real)\n', signames{1,i}, j-1);
                    fprintf(fid, '(assert (and (<= (- %s_t_m_dt_%d_end %s_t_m_dt_%d) %f) (>= (- %s_t_m_dt_%d_end %s_t_m_dt_%d) %f)))\n', signames{1,i}, j-1, signames{1,i}, j, delta, signames{1,i}, j-1, signames{1,i}, j, -delta);
                end
            end
            if signal_pref == 0
                check_t_minus_dt_constant(signames{1,i},k,dt,fid);
            else
                check_t_minus_dt_linear(signames{1,i},k,dt,fid);
            end
        end
    end
