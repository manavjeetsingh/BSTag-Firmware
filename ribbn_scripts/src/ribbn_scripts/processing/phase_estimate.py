import numpy as np
from matplotlib import pyplot as plt
import pickle
from ribbn_scripts.ref_functions.spec_functions import read_network_analyzer_file, get_theta, get_amplitude, s2z


# calibration_path="C:/Users/Manavjeet Singh/Git/T2TExperiments/coba/calibrations"
calibration_path="/Users/manavjeet/git/T2TExperiments/coba/calibrations"

def dbm_to_mV(dbm,Z=50):
    return 1000*np.sqrt(Z/1000)*(10**(dbm/20))





def multidist_multifreq_phase_estimation(freq_range, data_df, correction_factor, plot=False, three_phase=False, datapointsToUse=-1):
    """
        Correction factor could either be integer or dictionary.
        If integer, same offset is added to all freqs.
        If dict, it should contain offset for every freq.
    """
    # plt.figure(figsize=[20,45])
    if three_phase==True:
        ch_list = ['1', '4', '7']
    else:
        ch_list = ['1', '3', '4', '6', '7','8']
    all_freqs={}
    all_freqs_unwrapped={}
    all_freqs_theoretical={}
    all_freqs_theoretical_unwrapped={}
    all_errors={}
    g1,g2=[],[]
    selected_experiments_all={}
    for freq in freq_range:
    # for freq in [9.25e8]:
        theta12s={}
        errors={}
        for dist in sorted(data_df["dist"].unique()):
            # print("dist",dist)
            for exp_no in data_df[data_df["dist"]==dist]["Experiment Number"].unique():
                # print("Expno",data_df[data_df["dist"]==dist]["Experiment Number"].unique())
                selected_experiments=[]
                thetas=[]
                for rx in ["1","2"]:
                    if rx=="1":
                        tx="2"
                        rx_for_vna='5'
                        tx_for_vna='4'
                        rx_for_pv='1'
                    # else: # todo make this as an argument of the function
                    #     tx="1"
                    #     rx_for_vna='4'
                    #     tx_for_vna='5'
                    #     rx_for_pv='4'
                    else: # todo make this as an argument of the function
                        tx="1"
                        rx_for_vna='4'
                        tx_for_vna='5'
                        rx_for_pv='3'
                    
                    phases=[]
                    amps = []
                    attns = []
                    using_exp_no=None
                    for ch in ch_list:
                        # TODO: add another loop to distinguish between various experiments at the same/similar distances and various locations
                        
                        tx_dat = read_network_analyzer_file(
                            f'{calibration_path}/VNA_Oct2024/tag'+str(tx_for_vna)+'_channel_b\'' + f"ch_{str(ch)}" + '\'_vna_pwr_15.csv')
                        
                        rx_pv=pickle.load(open(f"{calibration_path}/PV_data_Aug2024/tag{rx_for_pv}_pv_polynomials_rx.pkl","rb"))
                        
                        sl_tx = tx_dat[1] * np.exp(1j * tx_dat[2])
                        gamma = (s2z(sl_tx) - s2z(np.conj(50))) / (s2z(sl_tx) + s2z(50))
                        gamma=1-gamma

                        # adc_out=bs_phase_df[(bs_phase_df["dist"]==dist) & (bs_phase_df["Rx"]==f"Tag{rx}") & (bs_phase_df[f"phase"]==ch) & (bs_phase_df["freq"]==freq) & (bs_phase_df["Experiment Number"]==exp_no)]["Rx Excitation (V)"].to_numpy()[0]
                        adc_out_df=data_df[(data_df["dist"]==dist) & (data_df["Rx"]==f"Tag{rx}") & (data_df[f"phase"]==ch) & (data_df["freq"]==freq) & (data_df["Experiment Number"]==exp_no)]
                        if datapointsToUse==-1:
                            # print("Using all datapoints")
                            try:
                                adc_out=float(adc_out_df.loc[adc_out_df['delta']==adc_out_df['delta'].min(),'median'].iloc[0]) # If there are multiple values at same freq and dist, take the one with minimum delta.
                            except Exception as e:
                                print(dist, freq)
                                print(adc_out_df)
                                raise e
                        else:
                            # print(f"Using {datapointsToUse} datapoints")
                            allVoltages=list(adc_out_df.loc[adc_out_df['delta']==adc_out_df['delta'].min(),'allVoltages'].iloc[0])
                            if datapointsToUse%2==0:
                                limitedVoltages=allVoltages[len(allVoltages)//2-datapointsToUse//2:len(allVoltages)//2+datapointsToUse//2]
                            else:
                                # limitedVoltages=allVoltages[len(allVoltages)//2-datapointsToUse//2:len(allVoltages)//2+datapointsToUse//2+1]
                                limitedVoltages=allVoltages[0:datapointsToUse]
                            # print("Length of adc_out",len(limitedVoltages))
                            adc_out=np.median(limitedVoltages)
                        # print("adc_out",adc_out)
                        
                        if using_exp_no==None:
                            using_exp_no=int(adc_out_df.loc[adc_out_df['delta']==adc_out_df['delta'].min(),'Unique Exp Number'].iloc[0])
                        else:
                            assert(using_exp_no==int(adc_out_df.loc[adc_out_df['delta']==adc_out_df['delta'].min(),'Unique Exp Number'].iloc[0]))
                        dbm_corrected=np.polyval(rx_pv[freq/1e6]["polynomial"],np.log(adc_out))
                        mV_corrected=dbm_to_mV(dbm_corrected)
                        amps.append(mV_corrected)

                        freq_a = tx_dat[0]
                        p = np.polyfit(freq_a, np.unwrap(np.angle(gamma)), 1)
                        phases.append(np.polyval(p, freq))

                        p = np.polyfit(freq_a, abs(gamma), 1)
                        attns.append(np.polyval(p, freq))

                    # print(len(amps), len(attns), len(phases))
                    th = get_theta(amps, attns, phases) 

                    thetas.append(th)
                    
                    selected_experiments.append(using_exp_no)

                assert(len(thetas)==2)
                # if thetas[0]<0.35 and thetas[1]>2.79:
                #     thetas[0]=np.pi-thetas[0]
                # elif thetas[0]>2.79 and thetas[1]<0.35:
                #     thetas[1]=np.pi-thetas[1]
                if isinstance(correction_factor, dict):
                    theta12=((thetas[0]+thetas[1])/2+correction_factor[freq])%np.pi
                else:
                    theta12=((thetas[0]+thetas[1])/2+correction_factor)%np.pi

                if dist not in theta12s.keys():
                    # theta12s[dist+correction_factor]=[]
                    # errors[dist+correction_factor]=[]
                    theta12s[dist]=[]
                    errors[dist]=[]
                # theta12s[dist+correction_factor].append(theta12)
                theta12s[dist].append(theta12)
                
                # Calculating error
                lambda_ = 3e8/freq
                # phi = 2*np.pi*(dist+correction_factor)/lambda_
                phi = 2*np.pi*(dist)/lambda_
                phi = phi % (np.pi)
                # errors[dist+correction_factor].append(abs(theta12-phi))
                _err=theta12-phi
                
                # NOTE: talking abs(_err) later
                errors[dist].append(_err)
                
                if exp_no not in selected_experiments_all:
                    selected_experiments_all[exp_no]=selected_experiments
                else:
                    selected_experiments_all[exp_no]=selected_experiments_all[exp_no]+selected_experiments
                
                

        all_freqs[int(freq)]=theta12s
        all_errors[int(freq)]=errors

        gt_dists=np.array(list(theta12s.keys()))
        my_unwrap={}
        for gt_dist_idx in range(0,len(gt_dists)):
            unwrapped_phases=[]
            # d=gt_dists[gt_dist_idx]+correction_factor
            d=gt_dists[gt_dist_idx]
            for idx,measured_phase in enumerate(theta12s[gt_dists[gt_dist_idx]]):
                lambda_ = 3e8/freq
                phi = 2*np.pi*d/lambda_
                pis = phi // (np.pi)
                #adjusting k based on ground truth
                if all_errors[freq][d][idx]>np.pi/2:
                    all_errors[freq][d][idx]-=np.pi
                    pis-=1
                elif all_errors[freq][d][idx]<-np.pi/2:
                    all_errors[freq][d][idx]+=np.pi
                    pis+=1
                
                all_errors[freq][d][idx]=np.abs(all_errors[freq][d][idx])
                    
                unwrapped_phases.append(measured_phase+pis*np.pi)
            my_unwrap[d]=unwrapped_phases
        all_freqs_unwrapped[int(freq)]=my_unwrap

        theoretical_phase={}
        theoretical_phase_unwrapped={}
        theoretical_dist=np.arange(0.1,2,0.01)
        lambda_=None
        for d in theoretical_dist:
            lambda_ = 3e8/freq
            phi = 2*np.pi*d/lambda_
            theoretical_phase_unwrapped[d]=phi
            phi = phi % (np.pi)
            theoretical_phase[d]=phi

        all_freqs_theoretical[int(freq)]=theoretical_phase
        all_freqs_theoretical_unwrapped[int(freq)]=theoretical_phase_unwrapped
        
    if plot==True:
        plt.figure(figsize=[20,25])

        for plot_no, freq in enumerate(list(all_freqs.keys())[::]):
            # if freq!=995e6:
            #     continue
            freq=int(freq)
            theoretical_phases=all_freqs_theoretical[freq]
            theoretical_phases_unwrapped=all_freqs_theoretical_unwrapped[freq]
            plt.subplot(len(freq_range)//2+2,4,2*plot_no+1)
            
            theoretical_phases_vals=rad2deg(np.array(list(theoretical_phases.values())))
            theoretical_phases_unwrapped_vals=rad2deg(np.array(list(theoretical_phases_unwrapped.values())))
            
            plt.plot(theoretical_phases.keys(),theoretical_phases_vals,'--',color="gray")
            # plt.plot(theoretical_phases_unwrapped.keys(),theoretical_phases_unwrapped_vals,'--',color="gray")
            plt.title(f"Freq: {freq//1e6}MHz")
            
            measured_distances_flattened=[]
            measured_phases_flattened=[]
            measured_phases_flattened_unwrapped=[]
            
            for d in all_freqs[freq].keys():
                for measured_phase in all_freqs[freq][d]:
                    measured_phases_flattened.append(measured_phase)
                    measured_distances_flattened.append(d)
                    # measured_phases_flattened[d]=(measured_phase+0.439203673205105)%np.pi
                    break
                
            for d in all_freqs_unwrapped[freq].keys():
                for measured_phase_unwrapped in all_freqs_unwrapped[freq][d]:
                    measured_phases_flattened_unwrapped.append(measured_phase_unwrapped)
                    break
            measured_phases_flattened=rad2deg(np.array(measured_phases_flattened))
            measured_phases_flattened_unwrapped=rad2deg(np.array(measured_phases_flattened_unwrapped))
            plt.plot(measured_distances_flattened,measured_phases_flattened, '.-',label="Measured phase (ph)")
            # plt.plot(measured_distances_flattened,measured_phases_flattened_unwrapped, '.-', label="ph+k*pi")
            plt.xlabel("Distance [m]")
            # plt.ylabel("Phase [$^\circ$]")
            plt.ylabel("Phase [degrees]")
            plt.legend()
            
            plt.subplot(len(freq_range)//2+2,4,2*plot_no+2)
            
            error_phases_flattened=[]
            for d in all_errors[freq].keys():
                for measured_errors in all_errors[freq][d]:
                    error_phases_flattened.append(measured_errors)
                    break
            error_phases_flattened=np.array(error_phases_flattened)
            error_phases_flattened=rad2deg(error_phases_flattened)
            mean_error=np.mean(error_phases_flattened)
            plt.plot(measured_distances_flattened,error_phases_flattened, '.-', label="Error")
            plt.title(f"Mean error: {np.round(mean_error,4)}")
            plt.ylim([0,180])
            # plt.ylabel("Error [$^\circ$]")
            plt.ylabel("Error [degrees]")
            plt.yticks(np.arange(0,180,20))
            plt.grid()
            plt.legend()    
            
            
        plt.tight_layout()


        
    
    return all_freqs, all_freqs_unwrapped, all_freqs_theoretical, all_freqs_theoretical_unwrapped, all_errors, selected_experiments_all


def rad2deg(angles):
    return angles*180/np.pi