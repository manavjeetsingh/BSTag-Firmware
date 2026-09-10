import numpy as np

def get_offset(measured_phases, freq):
    """
    Returns the offset and the error after offset correction.
    """
    lambda_ = 3e8/freq
    measured_phases_flattened=[]
    theoretical_phases_flattened=[]
    measured_distances_flattened=[]

    # Flattening measurements
    for dist in measured_phases.keys():
        for ph_idx,_ in enumerate(measured_phases[dist]):
            
            
            ph=((2*np.pi*dist)/lambda_)%np.pi
            theoretical_phases_flattened.append(ph)
            
            measured_phases_flattened.append(measured_phases[dist][ph_idx])
            measured_distances_flattened.append(dist)
            
            
            break
    
    measured_phases_flattened=np.array(measured_phases_flattened)
    theoretical_phases_flattened=np.array(theoretical_phases_flattened)


    # Selecting offset
    best_offset=None
    best_error=1e6
    error_list=None
    search_width_factor=1/2
    for angle_offset in np.arange(-search_width_factor*np.pi,
                            search_width_factor*np.pi, 0.01):
        offseted_phases=(measured_phases_flattened+angle_offset)%np.pi

        
        #estimate new error        
        # errors = np.abs((offseted_phases - theoretical_phases_flattened))
        
        errors=np.zeros(len(offseted_phases))
        for idx,val in enumerate(offseted_phases):
            err_val=offseted_phases[idx]-theoretical_phases_flattened[idx]

            if err_val>np.pi/2:
                err_val-=np.pi
            elif err_val<-np.pi/2:
                err_val+=np.pi
            errors[idx]=np.abs(err_val)
        
        mean_error = errors.mean()
        # mse = ((offseted_phases - theoretical_phases_flattened)**2).mean()
        # print(mse, angle_offset)
        if mean_error<best_error:
            best_error=mean_error
            error_list=errors
            best_offset=angle_offset
    return best_offset,best_error,error_list

def get_errors(measured_phases, freq):
    """
    Returns the phase error from the .
    """
    lambda_ = 3e8/freq
    measured_phases_flattened=[]
    theoretical_phases_flattened=[]
    measured_distances_flattened=[]

    # Flattening measurements
    for dist in measured_phases.keys():
        for ph_idx,_ in enumerate(measured_phases[dist]):
            
            
            ph=((2*np.pi*dist)/lambda_)%np.pi
            theoretical_phases_flattened.append(ph)
            
            measured_phases_flattened.append(measured_phases[dist][ph_idx])
            measured_distances_flattened.append(dist)
            
            
            break
    
    measured_phases_flattened=np.array(measured_phases_flattened)
    theoretical_phases_flattened=np.array(theoretical_phases_flattened)


    #estimate error        
    
    errors=np.zeros(len(measured_phases_flattened))
    for idx,val in enumerate(measured_phases_flattened):
        err_val=measured_phases_flattened[idx]-theoretical_phases_flattened[idx]

        if err_val>np.pi/2:
            err_val-=np.pi
        elif err_val<-np.pi/2:
            err_val+=np.pi
        # errors[idx]=np.abs(err_val)
        errors[idx]=err_val
    
    mean_error = errors.mean()

    
    return mean_error,errors,measured_distances_flattened