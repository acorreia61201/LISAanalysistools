from multiprocessing.sharedctypes import Value
import cupy as xp
import time
import pickle
import shutil
import numpy as np
from copy import deepcopy

# from lisatools.sampling.moves.gbspecialgroupstretch import GBSpecialGroupStretchMove

mempool = xp.get_default_memory_pool()

from eryn.ensemble import EnsembleSampler
from single_mcmc_run import run_single_band_search
from lisatools.utils.multigpudataholder import MultiGPUDataHolder
from eryn.moves import CombineMove
from eryn.moves.tempering import make_ladder
from eryn.state import State, BranchSupplimental
from lisatools.sampling.moves.specialforegroundmove import GBForegroundSpecialMove
from lisatools.sampling.prior import GBPriorWrap
from eryn.prior import ProbDistContainer
from gbgpu.gbgpu import GBGPU

import subprocess

import warnings
warnings.filterwarnings("ignore")

from lisatools.sampling.stopping import SearchConvergeStopping


from eryn.moves import Move
class PlaceHolder(Move):
    def __init__(self, *args, **kwargs):
        super(PlaceHolder, self).__init__(*args, **kwargs)

    def propose(self, model, state):
        accepted = np.zeros(state.log_like.shape)
        self.temperature_control.swaps_accepted = np.zeros(self.temperature_control.ntemps - 1)
        return state, accepted

def log_like(x, freqs, data, gb, df, data_length, supps=None, **sens_kwargs):
    if supps is None:
        raise ValueError("Must provide supps to identify the data streams.")

    wi = supps["walker_inds"]
    psd_pars = x[0]
    galfor_pars = x[1]
    A_data = data[0]
    E_data = data[1]
    
    data_index_all = xp.asarray(wi).astype(np.int32)
    ll = xp.zeros(psd_pars.shape[0]) 
    A_Soms_d_in_all = xp.asarray(psd_pars[:, 0])
    A_Sa_a_in_all = xp.asarray(psd_pars[:, 1])
    E_Soms_d_in_all = xp.asarray(psd_pars[:, 2])
    E_Sa_a_in_all = xp.asarray(psd_pars[:, 3])
    Amp_all = xp.asarray(galfor_pars[:, 0])
    alpha_all = xp.asarray(galfor_pars[:, 1])
    sl1_all = xp.asarray(galfor_pars[:, 2])
    kn_all = xp.asarray(galfor_pars[:, 3])
    sl2_all = xp.asarray(galfor_pars[:, 4])
    num_data = 1
    num_psds = psd_pars.shape[0]

    gb.psd_likelihood(ll, freqs, A_data, E_data, data_index_all,  A_Soms_d_in_all,  A_Sa_a_in_all,  E_Soms_d_in_all,  E_Sa_a_in_all, 
                     Amp_all,  alpha_all,  sl1_all,  kn_all, sl2_all, df, data_length, num_data, num_psds)
    
    # # galfor_pars = None
    # ll2 = xp.zeros_like(ll)
    # for i, (psd_pars_i, galfor_pars_i) in enumerate(zip(psd_pars, galfor_pars)):
    #     psd = [
    #         get_sensitivity(freqs, model=psd_pars_i[:2], foreground_params=galfor_pars_i, **sens_kwargs),
    #         get_sensitivity(freqs, model=psd_pars_i[2:], foreground_params=galfor_pars_i, **sens_kwargs)
    #     ]
    #     psd[0][0] = psd[0][1]
    #     psd[1][0] = psd[1][1]

    #     # inner_product = 4 * df * (xp.sum(data[0][wi].conj() * data[0][wi] / psd[0]) + xp.sum(data[1][wi].conj() * data[1][wi] / psd[1])).real
    #     inner_product = 4 * df * (xp.sum(data[0].conj() * data[0] / psd[0]) + xp.sum(data[1].conj() * data[1] / psd[1])).real
    #     ll2[i] = -1/2 * inner_product - xp.sum(xp.log(xp.asarray(psd)))
    # assert np.allclose(ll.get(), ll2.get())
    return ll.get()


class PSDwithGBPriorWrap:
    def __init__(self, gb, psd_prior, gb_prior=None, gb_params=None, walker_inds=None, walker_inds_map=None, gb_inds=None):
        self.gb = gb
        self.psd_prior = psd_prior
        self.gb_prior = gb_prior
        self.gb_params = gb_params
        self.walker_inds = walker_inds
        self.walker_inds_map = walker_inds_map
        self.gb_inds = gb_inds

        if (
            self.walker_inds is not None 
            or self.walker_inds_map is not None
            or self.gb_prior is not None
            or self.gb_params is not None
            or self.gb_inds is not None
        ):
            if (
                self.walker_inds is None 
                or self.walker_inds_map is None
                or self.gb_prior is None
                or self.gb_params is None
                or self.gb_inds is None
            ):
                raise ValueError("If providing walker inds, map, gb_params, or priors, must provide all.")

    def logpdf(self, coords, inds, supps=None, branch_supps=None):

        psd_pars = coords["psd"].reshape(-1, coords["psd"].shape[-1])
        galfor_pars = coords["galfor"].reshape(-1, coords["galfor"].shape[-1])

        if self.walker_inds is not None:
            assert supps is not None
            walker_inds_in = self.walker_inds[supps[:]["walker_inds"].flatten()]
            gb_inds_in = self.gb_inds[supps[:]["walker_inds"].flatten()]
            gb_params_in = self.gb_params[supps[:]["walker_inds"].flatten()][gb_inds_in]
            current_group_inds = np.repeat(np.arange(psd_pars.shape[0])[:, None], walker_inds_in.shape[1], axis=-1)
            noise_index_all = xp.asarray(current_group_inds[gb_inds_in]).astype(np.int32)

            A_Soms_d_in_all = xp.asarray(psd_pars[:, 0])
            A_Sa_a_in_all = xp.asarray(psd_pars[:, 1])
            E_Soms_d_in_all = xp.asarray(psd_pars[:, 2])
            E_Sa_a_in_all = xp.asarray(psd_pars[:, 3])
            Amp_all = xp.asarray(galfor_pars[:, 0])
            alpha_all = xp.asarray(galfor_pars[:, 1])
            sl1_all = xp.asarray(galfor_pars[:, 2])
            kn_all = xp.asarray(galfor_pars[:, 3])
            sl2_all = xp.asarray(galfor_pars[:, 4])
            num_f = len(gb_params_in)

            Sn_A = xp.zeros(num_f, dtype=xp.float64)
            Sn_E = xp.zeros(num_f, dtype=xp.float64)
            f0 = xp.asarray(gb_params_in[:, 1]) / 1e3
            
            if len(f0) > 0:
                self.gb.get_lisasens_val(Sn_A, Sn_E, f0, noise_index_all, A_Soms_d_in_all,  A_Sa_a_in_all,  E_Soms_d_in_all,  E_Sa_a_in_all, Amp_all,  alpha_all,  sl1_all,  kn_all, sl2_all, num_f)
            
            gb_logpdf_contrib = self.gb_prior["gb_fixed"].logpdf(gb_params_in, Sn_f=Sn_A)
            logpdf_contribution = np.zeros_like(gb_inds_in, dtype=np.float64)

            logpdf_contribution[gb_inds_in] = gb_logpdf_contrib.get()
            gb_logpdf = logpdf_contribution.sum(axis=-1)
            
        else:
            gb_logpdf = 0.0

        psd_logpdf = self.psd_prior["psd"].logpdf(psd_pars)
        galfor_logpdf = self.psd_prior["galfor"].logpdf(galfor_pars)

        all_logpdf = (gb_logpdf + psd_logpdf + galfor_logpdf).reshape(coords["psd"].shape[:2])
        if np.all(np.isnan(all_logpdf)):
            raise ValueError("All log prior are inf")
        elif np.any(np.isnan(all_logpdf)):
            all_logpdf[np.isnan(all_logpdf)] = -np.inf
        return all_logpdf

    #     self.base_prior.logpdf(x, psds=self.mgh.)

# def log_like(x, freqs, data, supps=None, **sens_kwargs):
#     if supps is None:
#         raise ValueError("Must provide supps to identify the data streams.")

#     wi = supps["walker_inds"]

#     if isinstance(x, list):
#         psd_pars = x[0]
#         galfor_pars = x[1]
#     else:
#         psd_pars = x
#         galfor_pars = None

#     psd = [
#         get_sensitivity(freqs, model=psd_pars[:2], foreground_params=galfor_pars, **sens_kwargs),
#         get_sensitivity(freqs, model=psd_pars[2:], foreground_params=galfor_pars, **sens_kwargs)
#     ]
#     psd[0][0] = psd[0][1]
#     psd[1][0] = psd[1][1]
#     breakpoint
#     inner_product = 4 * df * (xp.sum(data[0].reshape(-1, 786433)[wi].conj() * data[0].reshape(-1, 786433)[wi] / psd[0]) + xp.sum(data[1].reshape(-1, 786433)[wi].conj() * data[1].reshape(-1, 786433)[wi] / psd[1])).real
#     breakpoint()
#     ll = -1/2 * inner_product - xp.sum(xp.log(xp.asarray(psd)))
#     return ll.get()


from eryn.utils.updates import Update

class UpdateNewResidualsPSD(Update):
    def __init__(
        self, comm, head_rank, verbose=False
    ):
        self.comm = comm
        self.head_rank = head_rank
        self.verbose = verbose

    def __call__(self, iter, last_sample, sampler):
        
        if self.verbose:
            print("Sending psd update to head process.")

        update_dict = {
            "cc_A": last_sample.branches["psd"].coords[0, :, 0, :2].copy(),
            "cc_E": last_sample.branches["psd"].coords[0, :, 0, 2:].copy(),
            "cc_foreground_params": last_sample.branches["galfor"].coords[0, :, 0, :].copy(),
            "cc_ll": last_sample.log_like[0].copy(),
            "cc_lp": last_sample.log_prior[0].copy(),
            "last_state": last_sample
        }

        self.comm.send({"send": True, "receive": True}, dest=self.head_rank, tag=60)

        self.comm.send({"psd_update": update_dict}, dest=self.head_rank, tag=68)

        if self.verbose:
            print("PSD: requesting updated data from head process.")

        new_info = self.comm.recv(source=self.head_rank, tag=61)
        
        if self.verbose:
            print("Received new data from head process.")

        nwalkers_pe = last_sample.log_like.shape[1]
        generated_info = new_info.get_data_psd(n_gen_in=nwalkers_pe)
    
        data = generated_info["data"]
        psd = generated_info["psd"]
        
        sampler.log_like_fn.args[1][0][:] = xp.asarray(data[0])
        sampler.log_like_fn.args[1][1][:] = xp.asarray(data[1])

        if self.verbose:
            print("Finished subbing in new data.")

        # need new prior
        gb_inds_generate = generated_info["gb_inds"]
        gb_nleaves_max = new_info.gb_info["cc_params"].shape[1]
        sampler._priors["all_models_together"].gb_params = new_info.gb_info["cc_params"][gb_inds_generate] # [new_info.gb_info["cc_inds"][gb_inds_generate]]
        sampler._priors["all_models_together"].walker_inds = np.repeat(np.arange(nwalkers_pe)[:, None], gb_nleaves_max, axis=-1)# [new_info.gb_info["cc_inds"][gb_inds_generate]]
        sampler._priors["all_models_together"].walker_inds_map = gb_inds_generate
        sampler._priors["all_models_together"].gb_inds = new_info.gb_info["cc_inds"][gb_inds_generate] 
        
        new_lp = sampler.compute_log_prior(last_sample.branches_coords, inds=last_sample.branches_inds, supps=last_sample.supplimental)

        last_sample.log_prior[:] = new_lp[:]

        new_ll = sampler.compute_log_like(last_sample.branches_coords, inds=last_sample.branches_inds, supps=last_sample.supplimental, logp=last_sample.log_prior)[0]

        xp.get_default_memory_pool().free_all_blocks()

        last_sample.log_like[:] = new_ll[:]
        return


def run_psd_pe(gpu, comm, head_rank):

    gpus = [gpu]
    
    gf_information = comm.recv(source=head_rank, tag=46)

    psd_info = gf_information.psd_info
    xp.cuda.runtime.setDevice(gpus[0])

    nwalkers_pe = psd_info["pe_info"]["nwalkers"]
    ntemps_pe = psd_info["pe_info"]["ntemps"]

    priors = {"all_models_together": psd_info["priors"]}

    generated_info = gf_information.get_data_psd(include_ll=False, include_source_only_ll=False, n_gen_in=nwalkers_pe)
    
    if "gb_inds" in generated_info:

        gb_inds_generate = generated_info["gb_inds"]
        gb_nleaves_max = gf_information.gb_info["cc_params"].shape[1]
        gpu_priors_in = deepcopy(gf_information.gb_info["priors"].priors_in)
        for key, item in gpu_priors_in.items():
            item.use_cupy = True

        gpu_priors = {"gb_fixed": GBPriorWrap(gf_information.gb_info["pe_info"]["ndim"], ProbDistContainer(gpu_priors_in, use_cupy=True))}

        gb_params = gf_information.gb_info["cc_params"][gb_inds_generate] # [gf_information.gb_info["cc_inds"][gb_inds_generate]]
        walker_inds = np.repeat(np.arange(nwalkers_pe)[:, None], gb_nleaves_max, axis=-1)# [gf_information.gb_info["cc_inds"][gb_inds_generate]]
        walker_inds_map = gb_inds_generate
        gb_inds = gf_information.gb_info["cc_inds"][gb_inds_generate] 

    else:
        gb_params = None
        walker_inds = None
        walker_inds_map = None
        gpu_priors = None
        gb_inds = None

    gb = GBGPU(use_gpu=True)
    prior_wrap = {"all_models_together": PSDwithGBPriorWrap(
        gb, 
        priors["all_models_together"], 
        gb_prior=gpu_priors, 
        gb_params=gb_params, 
        walker_inds=walker_inds, 
        walker_inds_map=walker_inds_map,
        gb_inds=gb_inds,
    )}

    if "last_state" in psd_info:
        last_sample = psd_info["last_state"]
    else:
        coords_psd = prior_wrap["all_models_together"].psd_prior["psd"].rvs(size=(ntemps_pe, nwalkers_pe, 1))
        inds_psd = np.ones(coords_psd.shape[:-1], dtype=bool)
        coords_galfor = prior_wrap["all_models_together"].psd_prior["galfor"].rvs(size=(ntemps_pe, nwalkers_pe, 1))
        inds_galfor = np.ones(coords_galfor.shape[:-1], dtype=bool)
        last_sample = State({"psd": coords_psd, "galfor": coords_galfor}, inds={"psd": inds_psd, "galfor": inds_galfor})

    fd = xp.asarray(gf_information.general_info["fd"])
    data = [xp.asarray(generated_info["data"][0]), xp.asarray(generated_info["data"][1])]
    walker_vals = np.tile(np.arange(nwalkers_pe), (ntemps_pe, 1))

    supps_base_shape = (ntemps_pe, nwalkers_pe)
    supps = BranchSupplimental({"walker_inds": walker_vals}, base_shape=supps_base_shape, copy=True)

    check = prior_wrap["all_models_together"].logpdf(last_sample.branches_coords, last_sample.branches_inds, supps=supps)

    sens_kwargs = psd_info["psd_kwargs"].copy()
 
    if hasattr(last_sample, "betas") and last_sample.betas is not None:
        betas = last_sample.betas
    else:
        betas = make_ladder(sum(list(psd_info["pe_info"]["ndims"].values())), ntemps=ntemps_pe)

        last_sample.betas = betas

    assert not np.all(betas == 0.0)
    assert len(betas) == ntemps_pe

    branch_names = psd_info["pe_info"]["branch_names"]
    
    state_mix = State(
        {name: last_sample.branches_coords[name] for name in branch_names}, 
        inds={name: last_sample.branches_inds[name] for name in branch_names}, 
        supplimental=supps, 
        betas=betas
    )

    update = UpdateNewResidualsPSD(comm, head_rank, verbose=False)

    ndims_in = psd_info["pe_info"]["ndims"]
    nleaves_max_in = psd_info["pe_info"]["nleaves_max"]

    # TODO: fix this 
    df = gf_information.general_info["df"]
    data_length = gf_information.general_info["data_length"]

    if "run_search" in psd_info["search_info"] and psd_info["search_info"]["run_search"]:
        stopping_fn = psd_info["search_info"]["stopping_function"]
        if hasattr(stopping_fn, "add_comm"):
            stopping_fn.add_comm(comm)
        stopping_iterations = psd_info["search_info"]["stopping_iterations"]
        thin_by = psd_info["search_info"]["thin_by"]

    else:
        stopping_fn = None
        stopping_iterations = -1
        thin_by = psd_info["pe_info"]["thin_by"]

    sampler_mix = EnsembleSampler(
        nwalkers_pe,
        ndims_in,  # assumes ndim_max
        log_like,
        prior_wrap,
        tempering_kwargs={"betas": betas, "permute": False, "skip_swap_supp_names": ["walker_inds"]},
        nbranches=len(branch_names),
        nleaves_max=nleaves_max_in,
        kwargs=sens_kwargs,  # {"start_freq_ind": start_freq_ind, **waveform_kwargs},
        args=(fd, data, gb, df, data_length),
        backend=psd_info["reader"],
        vectorize=True,
        periodic=psd_info["periodic"],  # TODO: add periodic to proposals
        branch_names=branch_names,
        update_fn=update,  # sttop_converge_mix,
        update_iterations=psd_info["pe_info"]["update_iterations"],
        stopping_fn=stopping_fn,
        stopping_iterations=stopping_iterations,
        provide_groups=False,
        provide_supplimental=True,
    )

    lp = sampler_mix.compute_log_prior(state_mix.branches_coords, inds=state_mix.branches_inds, supps=state_mix.supplimental)
    ll = sampler_mix.compute_log_like(state_mix.branches_coords, inds=state_mix.branches_inds, supps=state_mix.supplimental, logp=lp)[0]

    state_mix.log_like = ll
    state_mix.log_prior = lp

    # equlibrating likelihood check: -4293090.6483655665,
    nsteps_mix = 50000

    print("Starting psd ll best:", state_mix.log_like.max(axis=-1))
    mempool.free_all_blocks()
    
    out = sampler_mix.run_mcmc(state_mix, nsteps_mix, progress=psd_info["pe_info"]["progress"], thin_by=thin_by, store=True)
    print("ending psd ll best:", out.log_like.max(axis=-1))

    # communicate end of run to head process
    comm.send({"finish_run": True}, dest=head_rank, tag=60)
    return

if __name__ == "__main__":
    import argparse
    """parser = argparse.ArgumentParser()

    parser.add_argument('--gpu', type=int,
                        help='which gpu', required=True)

    args = parser.parse_args()"""

    output = run_psd_pe(6)
                