import numpy as np
from matplotlib import pyplot as plt
import math
import copy
from scipy.stats import linregress
from sklearn.metrics import mean_squared_error
import pandas as pd
from collections import Counter
from sklearn.linear_model import LinearRegression


def myUnwrap(p,discont=None,axis=-1,period=2*np.pi):
    p = np.asarray(p)
    nd = p.ndim
    dd = np.diff(p, axis=axis)
    if discont is None:
        discont = period/2
    slice1 = [slice(None, None)]*nd     # full slices
    slice1[axis] = slice(1, None)
    slice1 = tuple(slice1)

    dtype = np.result_type(dd, period)

    if np.issubdtype(dtype, np.integer):
        interval_high, rem = divmod(period, 2)
        boundary_ambiguous = rem == 0
    else:
        interval_high = period / 2
        boundary_ambiguous = True
    interval_low = -interval_high
    ddmod = np.mod(dd - interval_low, period) + interval_low
    ddmod=np.where(ddmod < interval_low, ddmod+interval_high, ddmod)  
    if boundary_ambiguous:
        # for `mask = (abs(dd) == period/2)`, the above line made
        # `ddmod[mask] == -period/2`. correct these such that
        # `ddmod[mask] == sign(dd[mask])*period/2`.
        np.copyto(ddmod, interval_high,
                   where=(ddmod == interval_low) & (dd > 0))
    ph_correct = ddmod - dd
    np.copyto(ph_correct, 0, where=abs(dd) < discont)
    up = np.array(p, copy=True, dtype=dtype)
    up[slice1] = p[slice1] + ph_correct.cumsum(axis)
    return up


def unwrap_phases(all_freqs,plot):
    phase={}
    for freq in all_freqs:
        for distance in all_freqs[freq]:
            #NOTE: case with multiple experiments with same distance not handled
            if distance not in phase:
                phase[distance]=[]
            phase[distance].append(all_freqs[freq][distance][0])

    if plot:
        plt_no=0
        for d in phase:
            if plt_no%3==0:
                plt.show()
                plt.figure(figsize=(3*3,3))
            plt.subplot(1,3,plt_no%3+1)
            # print("wrapped",phase[d])
            fs=np.array(list(all_freqs.keys()))
            plt.plot(fs, phase[d],'.-', label="Wrapped")
            tmp_o=np.array(phase[d])
            tmp=np.unwrap(tmp_o,period=np.pi)
            # tmp=myUnwrap(tmp_o,period=np.pi)
            tmp=tmp-tmp[0]
            plt.plot(all_freqs.keys(), tmp,'.-', label="Unwrapped")
            theoretical=np.mod(2*np.pi*d*fs/3e8,np.pi)
            plt.plot(fs,theoretical,'--',color="gray")
            # print("Theoretical"theoretical)
            # print()
            plt.title(f"{d}")
            plt.xlabel("Freq (Hz)")
            plt.ylabel("Phase (radians, wrapped)")
            plt.tight_layout()
            plt.legend
            plt_no+=1
        plt.show()
            
                
            
    phase_unwrapped={}
    for d in phase:
        tmp=np.array(phase[d])
        tmp=np.unwrap(tmp,period=np.pi)
        # tmp=myUnwrap(tmp,period=np.pi)
        tmp=tmp-tmp[0]
        phase_unwrapped[d]=tmp
        if plot:
            plt.plot(all_freqs.keys(),tmp,label=f"{d}")
    if plot:
        plt.legend()
        plt.show()
    return phase_unwrapped


def get_se(lr_model,X,y):
    N = len(X)
    p = len(X[0]) + 1
    X_with_intercept = np.zeros(shape=(N, p), dtype=float)
    X_with_intercept[:, 0] = 1
    X_with_intercept[:, 1:p] = X

    # beta_hat = np.linalg.inv(X_with_intercept.T @ X_with_intercept) @ X_with_intercept.T @ y\
    beta_hat = np.array([lr_model.intercept_,lr_model.coef_[0]])
    
    y_hat = lr_model.predict(X)
    residuals = y - y_hat
    residual_sum_of_squares = residuals.T @ residuals
    sigma_squared_hat = residual_sum_of_squares / (N - p)
    var_beta_hat = np.linalg.inv(X_with_intercept.T @ X_with_intercept) * sigma_squared_hat
    for p_ in range(p):
        standard_error = var_beta_hat[p_, p_] ** 0.5
    
    #returns slope, interecept and se_error for slope.
    return beta_hat[1],beta_hat[0],var_beta_hat[1, 1] ** 0.5

def estimate_dph_df(unique_distsances,all_freqs,plot=False, priortize_ISM=False):
    """
        all_freqs: nested dict of phases at all [freq][dist] combination.
    """
    
    phase_unwrapped=unwrap_phases(all_freqs,plot)
    if plot:
        plt.figure(figsize=[20,15])
    dphidfs_minmax={}

    for idx,i in enumerate(sorted(unique_distsances)):
        all_fitting_freqs=list(all_freqs.keys())
        all_fitting_unwrapped_phases=list(phase_unwrapped[i])

        fitted_coeff=np.polyfit(all_fitting_freqs,all_fitting_unwrapped_phases,1)
        X=np.array(all_fitting_freqs)
        y=np.array(all_fitting_unwrapped_phases)
        slope_lrg, intercept, r, p, se_slope = linregress(x=X, y=y)

        if priortize_ISM:
            # retrain linregress with weights.
            lm = LinearRegression()
            sample_weight = np.ones(len(X)) 
            sample_weight[11:19]*=5000*se_slope*1e6
            X_shaped=X.reshape(-1,1)
            lm.fit(X_shaped, y, sample_weight)
            # Weighted slopes and se
            slope_lrg,intercept,se_slope=get_se(lm,X_shaped,y)
            

        slope_min,slope_max=(slope_lrg - se_slope)*1e6, (slope_lrg + se_slope)*1e6
        
        line=np.polyval(fitted_coeff,list(all_freqs.keys()))
        # slope of fitted line
        # slope=(line[-1]-line[0])*1e6/(list(all_freqs.keys())[-1]-list(all_freqs.keys())[0])
        dphidfs_minmax[i]=(slope_min,slope_lrg*1e6,slope_max)
        
        p1=np.array((list(all_freqs.keys())[-1],line[-1]))
        p2=np.array((list(all_freqs.keys())[0],line[0]))
        
        point_offsets=[]
        for offset_idx,p3 in enumerate(zip(all_freqs.keys(),phase_unwrapped[i])):
            p3=np.array(p3)
            d=np.linalg.norm(np.cross(p2-p1,p1-p3))/np.linalg.norm(p2-p1)
            point_offsets.append((offset_idx,d))
            
        point_offsets.sort(key = lambda x: x[1])
        
        if plot:
            # Plotting the fitted line and points    
            plt.subplot(5,math.ceil(len(unique_distsances)/5),idx+1)
            plt.plot(all_freqs.keys(),phase_unwrapped[i],"o")
            
            plt.plot(all_freqs.keys(),line,label="original line",color="red",linewidth=0.5)
            plt.plot(all_freqs.keys(),np.array(list(all_freqs.keys()))*slope_min*1e-6+intercept,label="Slope lower bound",color="black",linewidth=0.5)
            plt.plot(all_freqs.keys(),np.array(list(all_freqs.keys()))*slope_max*1e-6+intercept,label="Slope upper bound",color="black",linewidth=0.5)
            
            plt.title(f"Distance: {np.round(i,2)} m")
            plt.xlabel("Frequency (Hz)")
            plt.ylabel("Phase (Rad)")
            plt.grid()
            plt.legend()
            plt.tight_layout()
            
    
    return dphidfs_minmax


def direct_distance_estimate(dphidfs_min_max,plot=False,offset=0):
    """
    """

    all_estimated_dists={}
    expected_dists={} #used for plotting
    median_dists={}
    average_dist={}
    SLOP_RESOLUTION=1000
    
    for dist in sorted(dphidfs_min_max.keys()):
        # estimated_dists=[]
        # # Getting slope at equal intervel. Kinda uniform sampling.
        # for dpdf in np.linspace(dphidfs_min_max[dist][0],dphidfs_min_max[dist][2],SLOP_RESOLUTION):
        #     estimated_d=1.5e2*(dpdf+offset)/np.pi
        #     estimated_dists.append(estimated_d)

        # Assuming slope is from a gaussian distribution
        sd=abs(dphidfs_min_max[dist][1]-dphidfs_min_max[dist][0])
        mean=dphidfs_min_max[dist][1]
        dpdfs=np.random.normal(mean,sd,1000)
        estimated_dists=1.5e2*(dpdfs+offset)/np.pi

        all_estimated_dists[dist]=np.array(estimated_dists)
        expected_dists[dist]=1.5e2*(dphidfs_min_max[dist][1]+offset)/np.pi
        median_dists[dist]=np.median(all_estimated_dists[dist])
        average_dist[dist]=np.mean(all_estimated_dists[dist])

    real_dists=np.array(sorted(dphidfs_min_max.keys()))

    if plot:
        plt.figure(figsize=[20,8])
        plt.subplot(1,2,1)
        plt.plot(real_dists,'o',label="Real Distance")
        plt.plot(expected_dists.values(),'o',label="Estimated Distance")
        plt.yticks(np.arange(0,2.2,0.05))
        plt.legend()
        plt.xlabel("Experiment Number")
        plt.ylabel("Distance (m)")
        plt.grid(axis='y')
        plt.title(f"Mean Err: {np.round(np.mean(np.abs(real_dists-np.array(list(expected_dists.values())))),4)} RMSE: {np.round(np.sqrt(mean_squared_error(real_dists,np.array(list(expected_dists.values())))),4)}")

        plt.subplot(1,2,2)
        plt.plot(real_dists,real_dists-np.array(list(expected_dists.values())),'o',label="Error")
        plt.plot(real_dists,np.zeros(len(real_dists)),'--',label="Zero Error")
        plt.yticks(np.arange(-1,1,0.05))
        plt.xticks(np.arange(0.1,2.2,0.1))
        # plt.xticks(real_dists)
        plt.legend()
        # plt.xlabel("Experiment Number")
        # plt.ylabel("Distance (m)")
        plt.grid()
        plt.suptitle("center value")
        plt.show()
    
    return all_estimated_dists
def estimate_k(all_dist_estimates, all_freqs):
    """
    """
    FREQ=915e6
    freq_range=list(all_freqs.keys())
    phases_unwrapped=unwrap_phases(all_freqs,plot=False)
    k_estimates={} # dictionary of type real_dist: (real_k, [estimated ks...])
    for real_dist in all_dist_estimates.keys():
        estimated_ks=[]
        theta=phases_unwrapped[real_dist][freq_range.index(FREQ)]
        for est_dist in all_dist_estimates[real_dist]:
            k=np.round((est_dist-(1.5e8/(np.pi*FREQ)*theta))/(1.5e8/FREQ))
            estimated_ks.append(k)
        
        real_k=k=np.round((real_dist-(1.5e8/(np.pi*FREQ)*theta))/(1.5e8/FREQ))
        k_estimates[real_dist]=(real_k,estimated_ks)
        
    k_est_df=pd.DataFrame(columns=["Actual Dist","Actual K","Estimated Ks", "Estimated Ks count"])

    for dist in k_estimates:
        tup=k_estimates[dist]
        counter_inst=Counter(tup[1])
        df_row={ "Actual Dist": dist,
                "Actual K": tup[0],
                "Estimated Ks":counter_inst.keys(),
                "Estimated Ks count":counter_inst.values()
                }
        k_est_df=pd.concat([k_est_df,pd.DataFrame([df_row])],ignore_index=True)
        
    return k_estimates,k_est_df


def k_distance_estimate():
    """
    """
    pass