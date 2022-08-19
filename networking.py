import larpix
import larpix.io
import larpix.logger
import argparse
import time
import json
import re

_default_logger=False
_default_pacmanTile=2
_default_resetLength=64
_default_ioGroup=3
_default_verbose=False
_default_activeUser=False
_default_networkName=None
_default_disablePower=False
_default_tx_diff=0
_default_tx_slice=15
_default_ref_current_trim=16

def reconcile_configuration(c, chip_keys, verbose, \
                            timeout=0.1, connection_delay=0.01, \
                            n=2, n_verify=2):
    if isinstance(chip_keys, (str, larpix.key.Key)): chip_keys = [chip_keys]
    chip_key_register_pairs = [(chip_key, \
                                range(c[chip_key].config.num_registers)) \
                               for chip_key in chip_keys]
    return reconcile_registers(c, chip_key_register_pairs, verbose, \
                               timeout=timeout, \
                               connection_delay=connection_delay, \
                               n=n, n_verify=n_verify)



def reconcile_registers(c, chip_key_register_pairs, verbose, timeout=1, \
                        connection_delay=0.02, n=1, n_verify=1):
    ok, diff = c.verify_registers(chip_key_register_pairs, timeout=timeout, \
                                  connection_delay=connection_delay,
                                  n=n_verify)
    if diff!={}:
        flag = True
        for a in diff.keys():
            if flag == False: break
            for b in diff[a].keys():
                pair = diff[a][b]
                if verbose: print(a,'\t',n,':\t',b,'\t',pair)
                if pair[1]==None: flag=False; break
    if not ok:
        chip_key_register_pairs = [(chip_key, register) \
                                   for chip_key in diff \
                                   for register in diff[chip_key]]
        c.multi_write_configuration(chip_key_register_pairs, write_read=0, \
                                    connection_delay=connection_delay)
        if n!=1: ok,diff = reconcile_registers(c, chip_key_register_pairs, \
                                               verbose, timeout=timeout,
                                               connection_delay=connection_delay, \
                                               n=n-1, n_verify=n_verify)
        else: ok, diff = c.verify_registers(chip_key_register_pairs, \
                                            timeout=timeout, \
                                            connection_delay=connection_delay, \
                                            n=n_verify)
    return ok, diff



def power_registers(): # find power register addresses            
    adcs=['VDDA', 'IDDA', 'VDDD', 'IDDD']
    data = {}
    for i in range(1,9,1):
        l = []
        offset = 0
        for adc in adcs:
            if adc=='VDDD': offset = (i-1)*32+17
            if adc=='IDDD': offset = (i-1)*32+16
            if adc=='VDDA': offset = (i-1)*32+1
            if adc=='IDDA': offset = (i-1)*32
            l.append( offset )
        data[i] = l
    return data



def report_power(a, ioGroup): # print power to screen                         
    power = power_registers()
    adc_read = 0x00024001
    for i in power.keys():
        if i>2: continue
        val_vdda = a.get_reg(adc_read+power[i][0], io_group=ioGroup)
        val_idda = a.get_reg(adc_read+power[i][1], io_group=ioGroup)
        val_vddd = a.get_reg(adc_read+power[i][2], io_group=ioGroup)
        val_iddd = a.get_reg(adc_read+power[i][3], io_group=ioGroup)
        print('Tile ',i,
              ' VDDA:',(((val_vdda>>16)>>3)*4),
              'mV\tIDDA:',(((val_idda>>16)-(val_idda>>31)*65535)*500*0.001),
              'mA\tVDDD:',(((val_vddd>>16)>>3)*4),
              'mV\tIDDD:',(((val_iddd>>16)-(val_iddd>>31)*65535)*500*0.001),
              'mA')
    return



def enable_tile(pacmanTile, resetLength, ioGroup):
    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(relaxed=True)

    # invert POSI/PISO polarity (specific to LArPix-v2b preproduction tile)
    inversion_registers=[0x0301c, 0x0401c, 0x0501c, 0x0601c]
    if pacmanTile==2: inversion_registers=[0x0701c, 0x0801c, 0x0901c, 0x0a01c]
    for ir in inversion_registers:
        c.io.set_reg(ir, 0b11, io_group=ioGroup)

    # disable PACMAN UART POSI
    c.io.set_reg(0x18, 0b0, io_group=ioGroup)

    # uncomment for reset during power on
    #c.io.reset_larpix(length=20000000, io_group=ioGroup)
    
    # set MCLK to 10 MHz (and clock phase shift)
    c.io.set_reg(0x101c, 4, io_group=ioGroup)
    #c.io.set_reg(0x101c, 9, io_group=ioGroup) #setting mclk speed to 5 MHz
    #for ioc in range(5,9,1): # setting uart clock speed to 2.5 MHz
    #    c.io.set_uart_clock_ratio(ioc, 20, io_group=ioGroup)

    # enable global LArPix power
    c.io.set_reg(0x00000014, 1, io_group=ioGroup)
    
    vdda_dac=44500;
    vddd_dac=41000
    vdda_reg=0x00024130; vddd_reg=0x00024131
    if pacmanTile==2: vdda_reg=0x00024132; vddd_reg=0x00024133
    c.io.set_reg(vdda_reg, vdda_dac, io_group=ioGroup)
    c.io.set_reg(vddd_reg, vddd_dac, io_group=ioGroup)

    # enable power to tile
    if pacmanTile==1: c.io.set_reg(0x00000010, 0b1000000001, io_group=ioGroup)
    if pacmanTile==2: c.io.set_reg(0x00000010, 0b1000000010, io_group=ioGroup)

    time.sleep(1)
    c.io.reset_larpix(length=resetLength, io_group=ioGroup)
    report_power(c.io, ioGroup)
    return c, c.io



def disable_tile(io, pacmanTile, ioGroup):
    # VDDD set to 0 explicitly needed on rev4
    vdda_reg=0x00024130; vddd_reg=0x00024131
    if pacmanTile==2: vdda_reg=0x00024132; vddd_reg=0x00024133
    io.set_reg(vdda_reg, 0, io_group=ioGroup) 
    io.set_reg(vddd_reg, 0, io_group=ioGroup)

    # disable power to tile
    io.set_reg(0x00000010, 0b1100000000, io_group=ioGroup)

    # disable global LArPix power
    io.set_reg(0x00000014, 0, io_group=ioGroup)
    
    print('Tile disabled. Sleeping for 5 seconds.')
    time.sleep(5)

    report_power(io, ioGroup)

    

def network_ext_node(c, ioGroup, io_channels, io_channel_root_chip_id_map):
    for ioc in io_channels:
        c.add_network_node(ioGroup, ioc, c.network_names, 'ext', root=True)
        c.add_network_link(ioGroup, ioc, 'miso_us', \
                           ('ext', io_channel_root_chip_id_map[ioc]), 0)
        c.add_network_link(ioGroup, ioc, 'miso_ds', \
                           (io_channel_root_chip_id_map[ioc], 'ext'), 0)
        c.add_network_link(ioGroup, ioc, 'mosi', \
                           ('ext', io_channel_root_chip_id_map[ioc]), 0)
    return



def configure_chip_id(c, ioGroup, ioChannel, chipId):
    setup_key = larpix.key.Key(ioGroup, ioChannel, 1)
    if setup_key not in c.chips: c.add_chip(setup_key, version='2b')
    c[setup_key].config.chip_id = chipId
    c.write_configuration(setup_key, 'chip_id')
    c.remove_chip(setup_key)

    chip_key = larpix.key.Key(ioGroup, ioChannel, chipId)
    if chip_key not in c.chips: c.add_chip(chip_key, version='2b')
    c[chip_key].config.chip_id = chipId
    c.write_configuration(chip_key, 'chip_id')

    return chip_key



def disable_csa_trigger(c, chip_key, \
                        ref_current_trim=16):
    # non-physical 'empty' register
    c[chip_key].config.RESERVED=0
    c.write_configuration(chip_key,'RESERVED')

    # disable channel CSAs
    c[chip_key].config.csa_enable=[0]*64
    c.write_configuration(chip_key,'csa_enable')

    # mask channels
    c[chip_key].config.channel_mask=[1]*64
    c.write_configuration(chip_key,'channel_mask')

    c[chip_key].config.ref_current_trim=ref_current_trim
    c.write_configuration(chip_key,'ref_current_trim')
    return



def setup_root_chips(c, io, ioGroup, io_channel_root_chip_id_map, \
                     verbose, tx_diff=0, tx_slice=15, \
                     ref_current_trim=16, \
                     r_term=2, i_rx=8):
    root_keys=[]
    for ioc in io_channel_root_chip_id_map.keys():
        chip_key = configure_chip_id(c, ioGroup, ioc, \
                                     io_channel_root_chip_id_map[ioc])

        # configure receivers
        c[chip_key].config.enable_posi=[0]*4
        c[chip_key].config.enable_posi[1]=1
        c.write_configuration(chip_key, 'enable_posi')
        c[chip_key].config.r_term1=r_term
        c.write_configuration(chip_key, 'r_term1')
        c[chip_key].config.r_term0=r_term
        c.write_configuration(chip_key, 'r_term0')

        disable_csa_trigger(c, chip_key, \
                            ref_current_trim=ref_current_trim)
        
        # configure transmitters
        c[chip_key].config.enable_piso_downstream=[0]*4
        c[chip_key].config.enable_piso_downstream[0]=1
        c.write_configuration(chip_key, 'enable_piso_downstream')
        c[chip_key].config.enable_piso_upstream=[0]*4
        c.write_configuration(chip_key, 'enable_piso_upstream')
        c[chip_key].config.i_tx_diff0=tx_diff
        c.write_configuration(chip_key, 'i_tx_diff0')
        c[chip_key].config.tx_slices0=tx_slice
        c.write_configuration(chip_key, 'tx_slices0')

        # enable PACMAN POSI
        io.set_reg(0x18, 2**(ioc-1), io_group=ioGroup)
        
        c.read_configuration(chip_key,0,timeout=0.01)
        total = len(c.reads[-1])
        chip = len(c.reads[-1].extract('chip_id', chip_key=chip_key))
        if verbose:
            print(chip_key,': \t total packets {}\t', \
                  'chip packets {}'.format(total,chip))
        
        ok, diff = reconcile_configuration(c, chip_key, verbose)
        if ok:
            if chip_key not in c.chips: c.add_chip(chip_key, version='2b')
            root_keys.append(chip_key)
            print(chip_key,' configured')
        if not ok:
            print(chip_key,' NOT configured')
            c[chip_key].config.enable_posi=[1]*4
            c[chip_key].config.enable_posi[0]=0
            c.write_configuration(chip_key, 'enable_posi')
            c[chip_key].config.enable_piso_downstream=[0]*4
            c.write_configuration(chip_key, 'enable_piso_downstream')
            ok, diff = reconcile_configuration(c, chip_key, verbose)
            c.remove_chip(chip_key)
            
        # disable PACMAN POSI
        io.set_reg(0x18, 0, io_group=ioGroup)
    return root_keys#, waitlist



def find_daughter_id(parent_piso, parent_chip_id, parent_io_channel):
    if parent_piso==3: daughter_id = parent_chip_id-10
    if parent_piso==1: daughter_id = parent_chip_id+10
    if parent_piso==2: daughter_id = parent_chip_id+1
    if parent_piso==0: daughter_id = parent_chip_id-1
    return daughter_id



def setup_parent_piso_us(c, parent, daughter, verbose, tx_diff, tx_slice):
    if parent.chip_id - daughter.chip_id == 10: piso=3
    if parent.chip_id - daughter.chip_id == -10: piso=1
    if parent.chip_id - daughter.chip_id == -1: piso=2
    if parent.chip_id - daughter.chip_id == 1: piso=0
    if verbose: print('PARENT ',parent,'\tdaughter ',\
                      daughter,'==>\t enable PISO US ', piso)
    c[parent].config.enable_piso_upstream[piso]=1
    c.write_configuration(parent, 'enable_piso_upstream')
    if verbose: print(c[parent].config.enable_piso_upstream)

    registers_to_write=[]
    setattr(c[parent].config,f'i_tx_diff{piso}', tx_diff)
    registers_to_write.append(c[parent].config.register_map[f'i_tx_diff{piso}'])
    setattr(c[parent].config,f'tx_slices{piso}', tx_slice)
    registers_to_write.append(c[parent].config.register_map[f'tx_slices{piso}'])
    for reg in registers_to_write: c.write_configuration(parent, reg)

    #c[parent].config.enable_piso_upstream[piso]=1
    #c.write_configuration(parent, 'enable_piso_upstream')
    #if verbose: print(c[parent].config.enable_piso_upstream)
    return 



def disable_parent_piso_us(c, parent, daughter, verbose, tx_diff=15, tx_slice=0):
    if parent.chip_id - daughter.chip_id == 10: piso=3
    if parent.chip_id - daughter.chip_id == -10: piso=1
    if parent.chip_id - daughter.chip_id == -1: piso=2
    if parent.chip_id - daughter.chip_id == 1: piso=0
    if verbose: print('PARENT ',parent,'\tdaughter ',\
                      daughter,'==>\t disable PISO US ', piso)
    c[parent].config.enable_piso_upstream[piso]=0
    c.write_configuration(parent, 'enable_piso_upstream')
    if verbose: print(c[parent].config.enable_piso_upstream)
    registers_to_write=[]
    setattr(c[parent].config,f'i_tx_diff{piso}', tx_diff)
    registers_to_write.append(c[parent].config.register_map[f'i_tx_diff{piso}'])
    setattr(c[parent].config,f'tx_slices{piso}', tx_slice)
    registers_to_write.append(c[parent].config.register_map[f'tx_slices{piso}'])
    for reg in registers_to_write: c.write_configuration(parent, reg)
    return



def setup_parent_posi(c, parent, daughter, verbose, r_term, i_rx):
    if parent.chip_id - daughter.chip_id == 10: posi=0
    if parent.chip_id - daughter.chip_id == -10: posi=2
    if parent.chip_id - daughter.chip_id == -1: posi=3
    if parent.chip_id - daughter.chip_id == 1: posi=1
    if verbose: print('PARENT ',parent,'\tdaughter ',\
                      daughter,'==>\t enable POSI ', posi)
    c[parent].config.enable_posi[posi]=1
    c.write_configuration(parent, 'enable_posi')
    if verbose: print(c[parent].config.enable_posi)
    registers_to_write=[]
    setattr(c[parent].config,f'r_term{posi}', r_term)
    registers_to_write.append(c[parent].config.register_map[f'r_term{posi}'])
    setattr(c[parent].config,f'i_rx{posi}', i_rx)
    registers_to_write.append(c[parent].config.register_map[f'i_rx{posi}'])
    for reg in registers_to_write: c.write_configuration(parent, reg)
    return



def disable_parent_posi(c, parent, daughter, verbose):
    if parent.chip_id - daughter.chip_id == 10: posi=0
    if parent.chip_id - daughter.chip_id == -10: posi=2
    if parent.chip_id - daughter.chip_id == -1: posi=3
    if parent.chip_id - daughter.chip_id == 1: posi=1
    if verbose: print('PARENT ',parent,'\tdaughter ',\
                      daughter,'==>\t disable POSI ', posi)
    posi_list = c[parent].config.enable_posi # !!!! 
    if posi_list.count(1)==1: # !!!!
        c[parent].config.enable_posi=[1]*4 # !!!!
        c[parent].config.enable_posi[posi]=0 # !!!!
    else:
        c[parent].config.enable_posi[posi]=0
    c.write_configuration(parent, 'enable_posi')
    if verbose: print(c[parent].config.enable_posi)
    return



def setup_daughter_posi(c, parent, daughter, verbose, r_term, i_rx):
    if parent.chip_id - daughter.chip_id == 10: posi=2
    if parent.chip_id - daughter.chip_id == -10: posi=0
    if parent.chip_id - daughter.chip_id == -1: posi=1
    if parent.chip_id - daughter.chip_id == 1: posi=3
    if verbose: print('parent ',parent,'\tDAUGHTER ',\
                      daughter,'==>\t enable POSI ', posi)
    c[daughter].config.enable_posi=[0]*4
    c[daughter].config.enable_posi[posi]=1
    c.write_configuration(daughter, 'enable_posi')
    if verbose: print(c[daughter].config.enable_posi)
    registers_to_write=[]
    setattr(c[daughter].config,f'r_term{posi}', r_term)
    registers_to_write.append(c[daughter].config.register_map[f'r_term{posi}'])
    setattr(c[daughter].config,f'i_rx{posi}', i_rx)
    registers_to_write.append(c[daughter].config.register_map[f'i_rx{posi}'])
    for reg in registers_to_write: c.write_configuration(parent, reg)
    return
    
    

def setup_daughter_piso(c, parent, daughter, verbose, tx_diff, tx_slice):
    c[daughter].config.enable_piso_upstream=[0]*4
    c.write_configuration(daughter, 'enable_piso_upstream')
    if parent.chip_id - daughter.chip_id == 10: piso=1
    if parent.chip_id - daughter.chip_id == -10: piso=3
    if parent.chip_id - daughter.chip_id == -1: piso=0
    if parent.chip_id - daughter.chip_id == 1: piso=2
    if verbose: print('parent ',parent,'\tDAUGHTER ',daughter,\
                      '==>\t PISO DS ', piso)
    c[daughter].config.enable_piso_downstream=[0]*4
    c[daughter].config.enable_piso_downstream[piso]=1
    c.write_configuration(parent, 'enable_piso_downstream')
    if verbose: print(c[daughter].config.enable_piso_downstream)
    
    registers_to_write=[]
    setattr(c[daughter].config,f'i_tx_diff{piso}', tx_diff)
    registers_to_write.append(c[daughter].config.register_map[f'i_tx_diff{piso}'])
    setattr(c[daughter].config,f'tx_slices{piso}', tx_slice)
    registers_to_write.append(c[daughter].config.register_map[f'tx_slices{piso}'])
    for reg in registers_to_write: c.write_configuration(parent, reg)

#    c[daughter].config.enable_piso_downstream=[0]*4
#    c[daughter].config.enable_piso_downstream[piso]=1
#    c.write_configuration(parent, 'enable_piso_downstream')
#    if verbose: print(c[daughter].config.enable_piso_downstream)
    return piso
    
    

def append_upstream_chip_ids(io_channel, chip_id, waitlist):
    initial=len(waitlist)
    addendum=waitlist
    if io_channel in list(range(1,33,4)):
        for i in range(chip_id,31):
            addendum.add(i); addendum.add(i-10); addendum.add(i+10)
    if io_channel in list(range(2,33,4)):
        for i in range(chip_id,51):
            addendum.add(i); addendum.add(i+10)
    if io_channel in list(range(3,33,4)):
        for i in range(chip_id,81):
            addendum.add(i); addendum.add(i-10)
    if io_channel in list(range(4,33,4)):
        for i in range(chip_id,101):
            addendum.add(i); addendum.add(i-10); addendum.add(i+10)
    return addendum


    
def setup_initial_network(c, io, ioGroup, root_keys, verbose,
                          tx_diff=0, tx_slice=15, \
                          ref_current_trim=16, \
                          r_term=2, i_rx=8):
    waitlist=set()
    cnt_configured, cnt_nonconfigured=0,0
    firstIteration=True
    for root in root_keys:

        if firstIteration==False:
            print('\n CONFIGURED: ',cnt_configured,
                  '\t NON-CONFIGURED: ',cnt_nonconfigured)

        io.set_reg(0x18, 2**(root.io_channel-1), io_group=ioGroup)
        ok, diff = reconcile_configuration(c, root, verbose)
        if ok:
            cnt_configured+=1
            print('\n',root,'\tconfigured: ',cnt_configured,
                  '\t non-configured',cnt_nonconfigured)
        if not ok:
            waitlist = append_upstream_chip_ids(root.io_channel, \
                                                root.chip_id, waitlist)
            cnt_unconfigured = len(waitlist)
            print('Parent ',root,' failed to configure')
            print(root,'\tconfigured: ',cnt_configured,
                  '\t non-configured',cnt_nonconfigured)
            continue
        io.set_reg(0x18, 0, io_group=ioGroup)
        
        bail=False
        last_chip_id = root.chip_id
        while last_chip_id<=root.chip_id+9:
            if bail==True: break
            for parent_piso_us in [3,1,2]:
                print('\n')
                if bail==True: break
                daughter_id = find_daughter_id(parent_piso_us, last_chip_id, \
                                               root.io_channel)
#                if daughter_id==39: continue

                ck_ids=[]
                for ck in c.chips: ck_ids.append(ck.chip_id)
                if daughter_id in ck_ids: continue

                parent=larpix.key.Key(root.io_group, root.io_channel, \
                                      last_chip_id)

                daughter=larpix.key.Key(root.io_group, root.io_channel, \
                                        daughter_id)

                io.set_reg(0x18, 2**(root.io_channel-1), io_group=ioGroup)
                setup_parent_piso_us(c, parent, daughter, verbose, \
                                     tx_diff, tx_slice)

                ok, diff = reconcile_configuration(c, parent, verbose)
                if not ok:
                    print('\t\t==> Parent PISO US ',parent,\
                          ' failed to configure')
                    disable_parent_piso_us(c, parent, daughter, verbose)
                    waitlist = append_upstream_chip_ids(root.io_channel, \
                                                        daughter_id, \
                                                        waitlist)
                    cnt_nonconfigured = len(waitlist)
                    print(daughter,'\tconfigured: ',cnt_configured,
                          '\t non-configured',cnt_nonconfigured)
                    bail=True
                    continue

                daughter = configure_chip_id(c, root.io_group, \
                                             root.io_channel, daughter_id)
                setup_daughter_posi(c, parent, daughter, verbose, \
                                    r_term, i_rx)
                piso = setup_daughter_piso(c, parent, daughter, verbose, \
                                           tx_diff, tx_slice)
                setup_parent_posi(c, parent, daughter, verbose, \
                                  r_term, i_rx)
                disable_csa_trigger(c, daughter, \
                                    ref_current_trim=ref_current_trim)

                ok, diff = reconcile_configuration(c, daughter, verbose)
                if ok:
                    cnt_configured+=1
                    print(daughter,'\tconfigured: ',cnt_configured,
                          '\t non-configured',cnt_nonconfigured)
                if not ok:
                    print('\t\t==> Daughter',daughter,' failed to configure')
                    disable_parent_piso_us(c, parent, daughter, verbose)
                    disable_parent_posi(c, parent, daughter, verbose)

                    c.remove_chip(daughter) 
                    
                    if parent_piso_us==2:
                        waitlist = append_upstream_chip_ids(root.io_channel, \
                                                            daughter_id, \
                                                            waitlist)
                        bail=True
                    if parent_piso_us!=2: waitlist.add(daughter_id)
                    cnt_nonconfigured = len(waitlist)
                    print(daughter,'\tconfigured: ',cnt_configured,
                          '\t non-configured',cnt_nonconfigured)
                                        
                io.set_reg(0x18, 0, io_group=ioGroup)
                
            last_chip_id = daughter_id
            #print('last chip id: \t', last_chip_id,'\t bail status: ',bail)
            
        firstIteration=False
    print(len(c.chips),' CONFIGURED chips in network')
    return 



def find_waitlist(c):
    network = {}
    waitlist = []
    for chip_key in c.chips: network[chip_key.chip_id]=chip_key
    for chip_id in range(11,111):
        if chip_id not in network.keys(): waitlist.append(chip_id)
    return waitlist, network



def find_potential_parents(chip_id, network, verbose):
    parents=[]
    for i in [10,-10,1,-1]:
        if chip_id%10==0 and (chip_id+i)%10==1: continue
        if (chip_id+i)%10==0 and chip_id%10==1: continue
        if chip_id+i in network.keys(): parents.append(network[chip_id+i])
    return parents


    
def iterate_waitlist(c, io, ioGroup, activeUser, verbose,
                     tx_diff=0, tx_slice=15, \
                     ref_current_trim=16, \
                     r_term=2, i_rx=8):
    print('\n\n--------- Iterating waitlist ----------\n')
    flag=True; outstanding=[]
    while flag==True:
        waitlist, network = find_waitlist(c)
        n_waitlist = len(waitlist)
        if n_waitlist==0: flag=False
        outstanding=[]

        for chip_id in waitlist:
            potential_parents=find_potential_parents(chip_id, network, verbose)

            for parent in potential_parents:
                daughter=larpix.key.Key(parent.io_group, parent.io_channel, \
                                        chip_id)
#                if daughter.chip_id==39: continue

                if activeUser==True:
                    proceed=None
                    if activeUser==True:
                        print('\nParent ',parent,'\t Daughter ',daughter)
                        text='Ready to proceed (True) or skip (False)?\n'
                        proceed = input(text)
                    if proceed=='False' or proceed=='F' or proceed=='0': \
                       continue
                
                io.set_reg(0x18, 2**(parent.io_channel-1), io_group=ioGroup)
                
                setup_parent_piso_us(c, parent, daughter, verbose, \
                                            tx_diff, tx_slice)

                ok, diff = reconcile_configuration(c, parent, verbose)
                if not ok:
                    print('\t\t==> Parent PISO US ',parent,\
                          ' failed to configure')
                    disable_parent_piso_us(c, parent, daughter, verbose)
                    io.set_reg(0x18, 0, io_group=ioGroup)
                    continue                

                daughter = configure_chip_id(c, parent.io_group,\
                                             parent.io_channel, chip_id)
                setup_daughter_posi(c, parent, daughter, verbose, \
                                    r_term, i_rx)
                piso = setup_daughter_piso(c, parent, daughter, verbose, \
                                           tx_diff, tx_slice)
                setup_parent_posi(c, parent, daughter, verbose, \
                                  r_term, i_rx)
                disable_csa_trigger(c, daughter, \
                                    ref_current_trim=ref_current_trim)

                ok, diff = reconcile_configuration(c, daughter, verbose)
                if ok:
                    waitlist.remove(chip_id)
                    print('WAITLIST RESOLVED\t',daughter)
                    break # break out of potential parents loop
                if not ok:
                    print('\t\t==> Daughter',daughter,' failed to configure')
                    disable_parent_piso_us(c, parent, daughter, verbose)
                    disable_parent_posi(c, parent, daughter, verbose)
                    outstanding.append((daughter, piso))
                    c.remove_chip(daughter)
                io.set_reg(0x18, 0, io_group=ioGroup)
                
        if n_waitlist==len(waitlist):
            print('\n',len(waitlist),' NON-CONFIGURED chips\n',waitlist,'\n')
            flag=False
        else:
            print('\n\n********RE-TESTING ',len(waitlist),' CHIPs\n',waitlist)
            if activeUser==True:
                proceed=None
                if activeUser==True:
                    text='Continue iterating waitlist or exit early (False)?\n'
                    proceed = input(text)
                if proceed=='False' or proceed=='F' or proceed=='0': \
                   flag=False
    return outstanding



def configure_asic_network_links(c):
    for chip_key in c.chips:
        piso_us = c[chip_key].config.enable_piso_upstream
        for uart in range(len(piso_us)):
            if piso_us[uart]!=1: continue
            if uart==3: daughter_chip_id = chip_key.chip_id-10
            if uart==1: daughter_chip_id = chip_key.chip_id+10
            if uart==2: daughter_chip_id = chip_key.chip_id+1
            if uart==0: daughter_chip_id = chip_key.chip_id-1
            c.add_network_link(chip_key.io_group, chip_key.io_channel, \
                               'miso_us', \
                               (chip_key.chip_id, daughter_chip_id), uart)
        piso_ds = c[chip_key].config.enable_piso_downstream
        for uart in range(len(piso_ds)):
            if piso_ds[uart]!=1: continue
            if uart==3: daughter_chip_id = chip_key.chip_id-10
            if uart==1: daughter_chip_id = chip_key.chip_id+10
            if uart==2: daughter_chip_id = chip_key.chip_id+1
            if uart==0: daughter_chip_id = chip_key.chip_id-1
            c.add_network_link(chip_key.io_group, chip_key.io_channel, \
                               'miso_ds', \
                               (chip_key.chip_id, daughter_chip_id), uart)
        posi = c[chip_key].config.enable_posi
        for uart in range(len(posi)):
            if posi[uart]!=1: continue
            if uart==0: mother_chip_id = chip_key.chip_id-10
            if uart==2: mother_chip_id = chip_key.chip_id+10
            if uart==3: mother_chip_id = chip_key.chip_id+1
            if uart==1: mother_chip_id = chip_key.chip_id-1
            c.add_network_link(chip_key.io_group, chip_key.io_channel, \
                               'mosi', \
                               (chip_key.chip_id, mother_chip_id), uart)
    return c
            
                               

def miso_us_chip_id_list(chip2chip_pair, miso_us):
    if chip2chip_pair[0]=='ext' or chip2chip_pair[1]=='ext':
        miso_us[3]=chip2chip_pair[1]
        return miso_us
    if chip2chip_pair[1]-chip2chip_pair[0]==1:
        miso_us[3]=chip2chip_pair[1] # piso 2
    if chip2chip_pair[1]-chip2chip_pair[0]==-1:
        miso_us[1]=chip2chip_pair[1] # piso 0
    if chip2chip_pair[1]-chip2chip_pair[0]==-10:
        miso_us[0]=chip2chip_pair[1] # piso 3
    if chip2chip_pair[1]-chip2chip_pair[0]==10:
        miso_us[2]=chip2chip_pair[1] # piso 1
    return miso_us



def write_network_to_file(c, name, outstanding, \
                          ioGroup, pacmanTile, layout="2.5.0"):
    io_channels=list(range(1,5,1))
    if pacmanTile==2: io_channels=list(range(5,9,1))

    d=dict()
    d["_config_type"]="controller"
    d["name"]=name
    d["asic_version"]="2b"
    d["layout"]=layout

    c = configure_asic_network_links(c)
    d["network"]={}
    d["network"][ioGroup]={}
    for ioc in io_channels:
        d["network"][ioGroup][ioc]={}
        d["network"][ioGroup][ioc]["nodes"]=[]
        for node in list(c.network[ioGroup][ioc]['miso_us']):
            temp={}
            temp["chip_id"]=node
            miso_us=[None]*4
            for edge in list(c.network[ioGroup][ioc]['miso_us'].edges()):
                for chip2chip_pair in c.network[ioGroup][ioc]['miso_us'].edges(edge):
                    if chip2chip_pair[0]==node: miso_us_chip_id_list(chip2chip_pair, miso_us)
            temp["miso_us"]=miso_us
            if c.network[ioGroup][ioc]['miso_us'].nodes[node]['root']==True:
                temp["root"]=True
            d["network"][ioGroup][ioc]["nodes"].append(temp)
    d["network"]["miso_us_uart_map"]=[3,0,1,2]
    d["network"]["miso_ds_uart_map"]=[1,2,3,0]
    d["network"]["mosi_uart_map"]=[2,3,0,1]

    d["missing"]={}
    for pair in outstanding:
        key = pair[0]
        if key.io_group not in d["missing"]:
            d["missing"][key.io_group]={}
        if key.io_channel not in d["missing"][key.io_group]:
            d["missing"][key.io_group][key.io_channel]={}
        if key.chip_id not in d["missing"][key.io_group][key.io_channel]:
            d["missing"][key.io_group][key.io_channel][key.chip_id]=[]
        d["missing"][key.io_group][key.io_channel][key.chip_id].append( pair[1] )

    with open(name+'.json','w') as out:
        json.dump(d, out, indent=4)



def measure_csa_ibias(c, ioGroup):
    c.io = larpix.io.PACMAN_IO(relaxed=True)
    c.io.set_reg(0x25014, 2, io_group=ioGroup)
    c.io.set_reg(0x25015, 0x10, io_group=ioGroup)
    
    for chip in c.chips:
        print(chip)
        for i in range(4):
            text='Ready to proceed to quartile '+str(i)
            proceed=input(text)
            if proceed=='False' or proceed=='F' or proceed=='0': continue
            registers_to_write=[]
            setattr(c[chip].config,f'current_monitor_bank{i}',[0,0,0,1])
            registers_to_write.append(c[chip].config.register_map[f'current_monitor_bank{i}'])
            for reg in registers_to_write: c.write_configuration(chip, reg)
            
            text='Proceed to disable?'
            proceed=input(text)
            if proceed=='False' or proceed=='F' or proceed=='0': continue
            registers_to_write=[]
            setattr(c[chip].config,f'current_monitor_bank{i}',[0,0,0,0])
            registers_to_write.append(c[chip].config.register_map[f'current_monitor_bank{i}'])
            for reg in registers_to_write: c.write_configuration(chip, reg)
            
            text='Proceed to next quartile or chip?'
            proceed=input(text)
            if proceed=='False' or proceed=='F' or proceed=='0': continue

        text='Exit early? (Yes to exit)'
        proceed=input(text)
        if proceed=='Yes' or proceed=='Y': break
        
    c.io.set_reg(0x25014, 0x10, io_group=ioGroup)
    c.io.set_reg(0x25015, 0x10, io_group=ioGroup)    

    
    
def main(logger=_default_logger, pacmanTile=_default_pacmanTile, \
         resetLength=_default_resetLength, ioGroup=_default_ioGroup, \
         networkName=_default_networkName, verbose=_default_verbose, \
         activeUser=_default_activeUser, disablePower=_default_disablePower, \
         tx_diff=_default_tx_diff, tx_slice=_default_tx_slice, \
         ref_current_trim=_default_ref_current_trim):
    
    c, io = enable_tile(pacmanTile, resetLength, ioGroup)
    io.set_reg(0x25014,2,io_group=ioGroup)
    io.set_reg(0x25015,0x10,io_group=ioGroup)

    if logger==True:
        c.logger = larpix.logger.HDF5Logger()
        print('filename: ', c.logger.filename)
        c.logger.enable()

    io_channels=list(range(1,5,1))
    if pacmanTile==2: io_channels=list(range(5,9,1))
    io_channel_root_chip_id_map={}
    temp=[21,41,71,91]
    for i in range(len(io_channels)):
        io_channel_root_chip_id_map[io_channels[i]]=temp[i]

    network_ext_node(c, ioGroup, io_channels, io_channel_root_chip_id_map)

    root_keys = setup_root_chips(c, io, ioGroup, io_channel_root_chip_id_map, \
                                 verbose)
    print('ROOT KEYS:\t',root_keys)

    setup_initial_network(c, io, ioGroup, root_keys, verbose, \
                          tx_diff=tx_diff, tx_slice=tx_slice, \
                          ref_current_trim=ref_current_trim)
    
    nonconfigured = iterate_waitlist(c, io, ioGroup, activeUser, verbose, \
                                     tx_diff=tx_diff, tx_slice=tx_slice, \
                                     ref_current_trim=ref_current_trim)
    print('\n\n',nonconfigured)
    
    if logger==True: c.logger.flush(); c.logger.disable()
 
    if networkName!=None:
        write_network_to_file(c, networkName, nonconfigured, \
                              ioGroup, pacmanTile)

    if disablePower==True: disable_tile(io, pacmanTile, ioGroup)

    return c



if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--logger', default=_default_logger, \
                        type=bool, help='''Log packets to hdf5''')
    parser.add_argument('--pacmanTile', default=_default_pacmanTile, \
                        type=int, help='''PACMAN tile output to power''')
    parser.add_argument('--resetLength', default=_default_resetLength, \
                        type=int, help=''' Reset duration (MCLK cycles)''')
    parser.add_argument('--ioGroup', default=_default_ioGroup, \
                        type=int, help='''PACMAN IO group''')
    parser.add_argument('--networkName', default=_default_networkName, \
                        type=str, help='''Network name, if not instantiated \
                        no json file saved''')
    parser.add_argument('--verbose', default=_default_verbose, \
                        type=bool, help='''If true, print modified \
                        tested UARTs''')
    parser.add_argument('--activeUser', default=_default_activeUser, \
                        type=bool, help='''User interrupt between \
                        waitlist UART chip-to-chip test''')
    parser.add_argument('--disablePower', default=_default_disablePower, \
                        type=bool, help='''Disable power to tile ''')
    parser.add_argument('--tx_diff', default=_default_tx_diff, \
                        type=int, help='''Transmitter current per slice [DAC]''')
    parser.add_argument('--tx_slice', default=_default_tx_slice, \
                        type=int, help='''Transmitter current slices [DAC]''')
    parser.add_argument('--ref_current_trim', default=_default_ref_current_trim, \
                        type=int, help='''Master reference current [DAC]''')
    args = parser.parse_args()
    c = main(**vars(args))
