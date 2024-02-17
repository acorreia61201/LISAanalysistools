import warnings
from abc import ABC
from typing import Any, Tuple, Optional, List

import math
import numpy as np
from numpy.typing import ArrayLike
from scipy import interpolate
import matplotlib.pyplot as plt

try:
    import cupy as cp

except (ModuleNotFoundError, ImportError):
    import numpy as cp

from . import detector as lisa_models
from .utils.utility import AET
from .utils.constants import *
from .stochastic import (
    StochasticContribution,
    FittedHyperbolicTangentGalacticForeground,
)

"""
The sensitivity code is heavily based on an original code by Stas Babak, Antoine Petiteau for the LDC team.

References for noise models:
  * 'Proposal': LISA Consortium Proposal for L3 mission: LISA_L3_20170120 (https://atrium.in2p3.fr/13414ec1-c9ac-44b4-bace-7004468f684c)
  * 'SciRDv1': Science Requirement Document: ESA-L3-EST-SCI-RS-001 14/05/2018 (https://atrium.in2p3.fr/f5a78d3e-9e19-47a5-aa11-51c81d370f5f)
  * 'MRDv1': Mission Requirement Document: ESA-L3-EST-MIS-RS-001 08/12/2017
"""


class Sensitivity(ABC):
    """Base Class for PSD information."""

    @staticmethod
    def transform(
        f: float | np.ndarray,
        Spm: float | np.ndarray,
        Sop: float | np.ndarray,
        **kwargs: dict,
    ) -> float | np.ndarray:
        """Transform from the base sensitivity functions to the TDI PSDs.

        Args:
            f: Frequency array.
            Spm: Acceleration term.
            Sop: OMS term.
            **kwargs: For interoperability.

        Returns:
            Transformed TDI PSD values.

        """
        raise NotImplementedError

    @staticmethod
    def lisanoises(
        f: float | np.ndarray,
        model: Optional[lisa_models.LISAModel | str] = lisa_models.scirdv1,
        unit: Optional[str] = "relative_frequency",
    ) -> Tuple[float, float]:
        """Calculate both LISA noise terms based on input model.

        Args:
            f: Frequency array.
            model: Noise model. Object of type :class:`lisa_models.LISAModel`. It can also be a string corresponding to one of the stock models.
            unit: Either ``"relative_frequency"`` or ``"displacement"``.

        Returns:
            Tuple with acceleration term as first value and oms term as second value.

        """

        if isinstance(model, str):
            model = lisa_models.check_lisa_model(model)

        # TODO: fix this up
        Soms_d_in = model.Soms_d
        Sa_a_in = model.Sa_a

        frq = f
        ### Acceleration noise
        ## In acceleration
        Sa_a = Sa_a_in * (1.0 + (0.4e-3 / frq) ** 2) * (1.0 + (frq / 8e-3) ** 4)
        ## In displacement
        Sa_d = Sa_a * (2.0 * np.pi * frq) ** (-4.0)
        ## In relative frequency unit
        Sa_nu = Sa_d * (2.0 * np.pi * frq / C_SI) ** 2
        Spm = Sa_nu

        ### Optical Metrology System
        ## In displacement
        Soms_d = Soms_d_in * (1.0 + (2.0e-3 / f) ** 4)
        ## In relative frequency unit
        Soms_nu = Soms_d * (2.0 * np.pi * frq / C_SI) ** 2
        Sop = Soms_nu

        if unit == "displacement":
            return Sa_d, Soms_d
        elif unit == "relative_frequency":
            return Spm, Sop

    @classmethod
    def get_Sn(
        cls,
        f: float | np.ndarray,
        model: Optional[lisa_models.LISAModel | str] = lisa_models.scirdv1,
        **kwargs: dict,
    ) -> float | np.ndarray:
        """Calculate the PSD

        Args:
            f: Frequency array.
            model: Noise model. Object of type :class:`lisa_models.LISAModel`. It can also be a string corresponding to one of the stock models.
            **kwargs: For interoperability.

        Returns:
            PSD values.

        """
        x = 2.0 * np.pi * lisaLT * f

        # get noise values
        Spm, Sop = cls.lisanoises(f, model)

        # transform as desired for TDI combination
        Sout = cls.transform(f, Spm, Sop, **kwargs)

        # will add zero if ignored
        Sout += cls.get_stochastic_contribution(f, **kwargs)
        return Sout

    @classmethod
    def get_stochastic_contribution(
        cls,
        f: float | np.ndarray,
        stochastic_params: Optional[tuple] = (),
        stochastic_kwargs: Optional[dict] = {},
        stochastic_function: Optional[StochasticContribution | str] = None,
    ) -> float | np.ndarray:
        """Calculate contribution from stochastic signal.

        This function directs and wraps the calculation of and returns
        the stochastic signal. The ``stochastic_function`` calculates the
        sensitivity contribution. The ``transform_factor`` can transform that
        output to the correct TDI contribution.

        Args:
            f: Frequency array.
            stochastic_params: Parameters (arguments) to feed to ``stochastic_function``.
            stochastic_kwargs: Keyword arguments to feeed to ``stochastic_function``.
            stochastic_function: Stochastic class or string name of stochastic class. Takes ``stochastic_args`` and ``stochastic_kwargs``.
                If ``None``, it uses :class:`FittedHyperbolicTangentGalacticForeground`.

        Returns:
            Contribution from stochastic signal.


        """

        if isinstance(f, float):
            f = np.ndarray([f])
            squeeze = True
        else:
            squeeze = False

        sgal = np.zeros_like(f)

        if (
            (stochastic_params != () and stochastic_params is not None)
            or (stochastic_kwargs != {} and stochastic_kwargs is not None)
            or stochastic_function is not None
        ):
            if stochastic_function is None:
                stochastic_function = FittedHyperbolicTangentGalacticForeground

            try:
                check = stochastic_function.get_Sh(
                    f, *stochastic_params, **stochastic_kwargs
                )
            except:
                breakpoint()
            sgal[:] = check

        if squeeze:
            sgal = sgal.squeeze()
        return sgal

    @staticmethod
    def stochastic_transform(
        f: float | np.ndarray, Sh: float | np.ndarray, **kwargs: dict
    ) -> float | np.ndarray:
        """Transform from the base stochastic functions to the TDI PSDs.

        **Note**: If not implemented, the transform will return the input.

        Args:
            f: Frequency array.
            Sh: Power spectral density in stochastic term.
            **kwargs: For interoperability.

        Returns:
            Transformed TDI PSD values.

        """
        return Sh


class X1TDISens(Sensitivity):
    @staticmethod
    def transform(
        f: float | np.ndarray,
        Spm: float | np.ndarray,
        Sop: float | np.ndarray,
        **kwargs: dict,
    ) -> float | np.ndarray:
        __doc__ = (
            "Transform from the base sensitivity functions to the XYZ TDI PSDs.\n\n"
            + Sensitivity.transform.__doc__.split("PSDs.\n\n")[-1]
        )

        x = 2.0 * np.pi * lisaLT * f
        return 16.0 * np.sin(x) ** 2 * (2.0 * (1.0 + np.cos(x) ** 2) * Spm + Sop)

    @staticmethod
    def stochastic_transform(
        f: float | np.ndarray, Sh: float | np.ndarray, **kwargs: dict
    ) -> float | np.ndarray:
        __doc__ = (
            "Transform from the base stochastic functions to the XYZ stochastic TDI information.\n\n"
            + Sensitivity.stochastic_transform.__doc__.split("PSDs.\n\n")[-1]
        )
        x = 2.0 * np.pi * lisaLT * f
        t = 4.0 * x**2 * np.sin(x) ** 2
        return Sh * t


class Y1TDISens(X1TDISens):
    __doc__ = X1TDISens.__doc__
    pass


class Z1TDISens(X1TDISens):
    __doc__ = X1TDISens.__doc__
    pass


class XY1TDISens(Sensitivity):
    @staticmethod
    def transform(
        f: float | np.ndarray,
        Spm: float | np.ndarray,
        Sop: float | np.ndarray,
        **kwargs: dict,
    ) -> float | np.ndarray:
        __doc__ = (
            "Transform from the base sensitivity functions to the XYZ TDI PSDs.\n\n"
            + Sensitivity.transform.__doc__.split("PSDs.\n\n")[-1]
        )

        x = 2.0 * np.pi * lisaLT * f
        ## TODO Check the acceleration noise term
        return -4.0 * np.sin(2 * x) * np.sin(x) * (Sop + 4.0 * Spm)

    @staticmethod
    def stochastic_transform(
        f: float | np.ndarray, Sh: float | np.ndarray, **kwargs: dict
    ) -> float | np.ndarray:
        __doc__ = (
            "Transform from the base stochastic functions to the XYZ stochastic TDI information.\n\n"
            + Sensitivity.stochastic_transform.__doc__.split("PSDs.\n\n")[-1]
        )
        x = 2.0 * np.pi * lisaLT * f
        # TODO: check these functions
        # GB = -0.5 of X
        t = -0.5 * (4.0 * x**2 * np.sin(x) ** 2)
        return Sh * t


class ZX1TDISens(XY1TDISens):
    __doc__ = XY1TDISens.__doc__
    pass


class YZ1TDISens(XY1TDISens):
    __doc__ = XY1TDISens.__doc__
    pass


class X2TDISens(Sensitivity):
    @staticmethod
    def transform(
        f: float | np.ndarray,
        Spm: float | np.ndarray,
        Sop: float | np.ndarray,
        **kwargs: dict,
    ) -> float | np.ndarray:
        __doc__ = (
            "Transform from the base sensitivity functions to the XYZ TDI PSDs.\n\n"
            + Sensitivity.transform.__doc__.split("PSDs.\n\n")[-1]
        )

        x = 2.0 * np.pi * lisaLT * f
        ## TODO Check the acceleration noise term
        return (64.0 * np.sin(x) ** 2 * np.sin(2 * x) ** 2 * Sop) + (
            256.0 * (3 + np.cos(2 * x)) * np.cos(x) ** 2 * np.sin(x) ** 4 * Spm
        )

    @staticmethod
    def stochastic_transform(
        f: float | np.ndarray, Sh: float | np.ndarray, **kwargs: dict
    ) -> float | np.ndarray:
        __doc__ = (
            "Transform from the base stochastic functions to the XYZ stochastic TDI information.\n\n"
            + Sensitivity.stochastic_transform.__doc__.split("PSDs.\n\n")[-1]
        )
        x = 2.0 * np.pi * lisaLT * f
        # TODO: check these functions for TDI2
        t = 4.0 * x**2 * np.sin(x) ** 2
        return Sh * t


class Y2TDISens(X2TDISens):
    __doc__ = X2TDISens.__doc__
    pass


class Z2TDISens(X2TDISens):
    __doc__ = X2TDISens.__doc__
    pass


class A1TDISens(Sensitivity):
    @staticmethod
    def transform(
        f: float | np.ndarray,
        Spm: float | np.ndarray,
        Sop: float | np.ndarray,
        **kwargs: dict,
    ) -> float | np.ndarray:
        __doc__ = (
            "Transform from the base sensitivity functions to the A,E TDI PSDs.\n\n"
            + Sensitivity.transform.__doc__.split("PSDs.\n\n")[-1]
        )

        x = 2.0 * np.pi * lisaLT * f
        return (
            8.0
            * np.sin(x) ** 2
            * (
                2.0 * Spm * (3.0 + 2.0 * np.cos(x) + np.cos(2 * x))
                + Sop * (2.0 + np.cos(x))
            )
        )

    @staticmethod
    def stochastic_transform(
        f: float | np.ndarray, Sh: float | np.ndarray, **kwargs: dict
    ) -> float | np.ndarray:
        __doc__ = (
            "Transform from the base stochastic functions to the XYZ stochastic TDI information.\n\n"
            + Sensitivity.stochastic_transform.__doc__.split("PSDs.\n\n")[-1]
        )
        x = 2.0 * np.pi * lisaLT * f
        t = 4.0 * x**2 * np.sin(x) ** 2
        return 1.5 * (Sh * t)


class E1TDISens(A1TDISens):
    __doc__ = A1TDISens.__doc__
    pass


class T1TDISens(Sensitivity):
    @staticmethod
    def transform(
        f: float | np.ndarray,
        Spm: float | np.ndarray,
        Sop: float | np.ndarray,
        **kwargs: dict,
    ) -> float | np.ndarray:
        __doc__ = (
            "Transform from the base sensitivity functions to the T TDI PSDs.\n\n"
            + Sensitivity.transform.__doc__.split("PSDs.\n\n")[-1]
        )

        x = 2.0 * np.pi * lisaLT * f
        return (
            16.0 * Sop * (1.0 - np.cos(x)) * np.sin(x) ** 2
            + 128.0 * Spm * np.sin(x) ** 2 * np.sin(0.5 * x) ** 4
        )

    @staticmethod
    def stochastic_transform(
        f: float | np.ndarray, Sh: float | np.ndarray, **kwargs: dict
    ) -> float | np.ndarray:
        __doc__ = (
            "Transform from the base stochastic functions to the XYZ stochastic TDI information.\n\n"
            + Sensitivity.stochastic_transform.__doc__.split("PSDs.\n\n")[-1]
        )
        x = 2.0 * np.pi * lisaLT * f
        t = 4.0 * x**2 * np.sin(x) ** 2
        return 0.0 * (Sh * t)


class LISASens(Sensitivity):
    @classmethod
    def get_Sn(
        cls,
        f: float | np.ndarray,
        model: Optional[lisa_models.LISAModel | str] = lisa_models.scirdv1,
        average: bool = True,
        **kwargs: dict,
    ) -> float | np.ndarray:
        """Compute the base LISA sensitivity function.

        Args:
            f: Frequency array.
            model: Noise model. Object of type :class:`lisa_models.LISAModel`. It can also be a string corresponding to one of the stock models.
            average: Whether to apply averaging factors to sensitivity curve.
                Antenna response: ``av_resp = np.sqrt(5) if average else 1.0``
                Projection effect: ``Proj = 2.0 / np.sqrt(3) if average else 1.0``
            **kwargs: Keyword arguments to pass to :func:`get_stochastic_contribution`. # TODO: fix

        Returns:
            Sensitivity array.

        """

        # get noise values
        Sa_d, Sop = cls.lisanoises(f, model, unit="displacement")

        all_m = np.sqrt(4.0 * Sa_d + Sop)
        ## Average the antenna response
        av_resp = np.sqrt(5) if average else 1.0

        ## Projection effect
        Proj = 2.0 / np.sqrt(3) if average else 1.0

        ## Approximative transfer function
        f0 = 1.0 / (2.0 * lisaLT)
        a = 0.41
        T = np.sqrt(1 + (f / (a * f0)) ** 2)
        sens = (av_resp * Proj * T * all_m / lisaL) ** 2

        # will add zero if ignored
        sens += cls.get_stochastic_contribution(f, **kwargs)
        return sens


class CornishLISASens(LISASens):
    """PSD from https://arxiv.org/pdf/1803.01944.pdf

    Power Spectral Density for the LISA detector assuming it has been active for a year.
    I found an analytic version in one of Niel Cornish's paper which he submitted to the arXiv in
    2018. I evaluate the PSD at the frequency bins found in the signal FFT.

    PSD obtained from: https://arxiv.org/pdf/1803.01944.pdf

    """

    @staticmethod
    def get_Sn(
        f: float | np.ndarray, average: bool = True, **kwargs: dict
    ) -> float | np.ndarray:
        # TODO: documentation here

        sky_averaging_constant = 20.0 / 3.0 if average else 1.0

        L = 2.5 * 10**9  # Length of LISA arm
        f0 = 19.09 * 10 ** (-3)  # transfer frequency

        # Optical Metrology Sensor
        Poms = ((1.5e-11) * (1.5e-11)) * (1 + np.power((2e-3) / f, 4))

        # Acceleration Noise
        Pacc = (
            (3e-15)
            * (3e-15)
            * (1 + (4e-4 / f) * (4e-4 / f))
            * (1 + np.power(f / (8e-3), 4))
        )

        # constants for Galactic background after 1 year of observation
        alpha = 0.171
        beta = 292
        k = 1020
        gamma = 1680
        f_k = 0.00215

        # Galactic background contribution
        Sc = (
            9e-45
            * np.power(f, -7 / 3)
            * np.exp(-np.power(f, alpha) + beta * f * np.sin(k * f))
            * (1 + np.tanh(gamma * (f_k - f)))
        )

        # PSD
        PSD = (sky_averaging_constant) * (
            (10 / (3 * L * L))
            * (Poms + (4 * Pacc) / (np.power(2 * np.pi * f, 4)))
            * (1 + 0.6 * (f / f0) * (f / f0))
            + Sc
        )

        return PSD


class FlatPSDFunction(LISASens):
    """White Noise PSD function."""

    @staticmethod
    def get_Sn(f: float | np.ndarray, val: float, **kwargs: dict) -> float | np.ndarray:
        # TODO: documentation here
        out = np.full_like(f, val)
        if isinstance(f, float):
            out = out.item()
        return out


class SensitivityMatrix:
    """Container to hold sensitivity information.

    Args:
        f: Frequency array.
        sens_mat: Input sensitivity list. The shape of the nested lists should represent the shape of the
            desired matrix. Each entry in the list must be an array, :class:`Sensitivity`-derived object,
            or a string corresponding to the :class:`Sensitivity` object.
        **sens_kwargs: Keyword arguments to pass to :method:`Sensitivity.get_Sn`.

    """

    def __init__(
        self,
        f: np.ndarray,
        sens_mat: List[List[np.ndarray | Sensitivity]]
        | List[np.ndarray | Sensitivity]
        | np.ndarray
        | Sensitivity,
        *sens_args: tuple,
        **sens_kwargs: dict,
    ) -> None:
        self.frequency_arr = f
        self.data_length = len(self.frequency_arr)
        self.sens_args = sens_args
        self.sens_kwargs = sens_kwargs
        self.sens_mat = sens_mat

    @property
    def frequency_arr(self) -> np.ndarray:
        return self._frequency_arr

    @frequency_arr.setter
    def frequency_arr(self, frequency_arr: np.ndarray) -> None:
        assert frequency_arr.dtype == np.float64 or frequency_arr.dtype == float
        assert frequency_arr.ndim == 1
        self._frequency_arr = frequency_arr

    @property
    def sens_mat(self) -> np.ndarray:
        return self._sens_mat

    @sens_mat.setter
    def sens_mat(
        self, sens_mat: List[List[np.ndarray]] | List[np.ndarray] | np.ndarray
    ) -> None:
        self._sens_mat_input = sens_mat
        self._sens_mat = np.asarray(sens_mat, dtype=object)

        # not an
        np.zeros(len(self._sens_mat.flatten()), dtype=object)
        new_out = np.full(len(self._sens_mat.flatten()), None, dtype=object)
        self.return_shape = self._sens_mat.shape
        for i in range(len(self._sens_mat.flatten())):
            current_sens = self._sens_mat.flatten()[i]
            if hasattr(current_sens, "get_Sn") or isinstance(current_sens, str):
                new_out[i] = get_sensitivity(
                    self.frequency_arr,
                    *self.sens_args,
                    sens_fn=current_sens,
                    **self.sens_kwargs,
                )

            elif isinstance(current_sens, np.ndarray) or isinstance(
                current_sens, cp.npdarray
            ):
                new_out[i] = current_sens
            else:
                raise ValueError

        self._sens_mat = np.asarray(list(new_out), dtype=float).reshape(
            self.return_shape + (-1,)
        )

    def __getitem__(self, index: tuple) -> np.ndarray:
        return self.sens_mat[index]

    @property
    def ndim(self) -> int:
        return self.sens_mat.ndim

    def flatten(self) -> np.ndarray:
        return self.sens_mat.reshape(-1, self.sens_mat.shape[-1])

    @property
    def shape(self) -> tuple:
        return self.sens_mat.shape

    def loglog(
        self,
        ax: Optional[plt.Axes] = None,
        fig: Optional[plt.Figure] = None,
        inds: Optional[int | tuple] = None,
        char_strain: Optional[bool] = False,
        **kwargs: dict,
    ) -> Tuple[plt.Figure, plt.Axes]:
        """Produce a log-log plot of the sensitivity.

        Args:
            ax: Matplotlib Axes objects to add plots. Either a list of Axes objects or a single Axes object.
            fig: Matplotlib figure object.
            inds: Integer index to select out which data to add to a single access.
                A list can be provided if ax is a list. They must be the same length.
            char_strain: If ``True``, plot in characteristic strain representation. **Note**: assumes the sensitivity
                is input as power spectral density.
            **kwargs: Keyword arguments to be passed to ``loglog`` function in matplotlib.

        Returns:
            Matplotlib figure and axes objects in a 2-tuple.


        """
        if ax is None and fig is None:
            outer_shape = self.shape[:-1]
            if len(outer_shape) == 2:
                nrows = outer_shape[0]
                ncols = outer_shape[1]
            elif len(outer_shape) == 1:
                nrows = 1
                ncols = outer_shape[0]

            fig, ax = plt.subplots(nrows, ncols, sharex=True, sharey=True)
            ax = ax.ravel()

        elif ax is not None:
            assert len(ax) == np.prod(self.shape[:-1])

        elif fig is not None:
            raise NotImplementedError

        for i in range(np.prod(self.shape[:-1])):
            plot_in = self.flatten()[i]
            if char_strain:
                plot_in = np.sqrt(self.frequency_arr * plot_in)
            ax[i].loglog(self.frequency_arr, plot_in, **kwargs)

        return (fig, ax)


class XYZ1SensitivityMatrix(SensitivityMatrix):
    def __init__(self, f: np.ndarray, **sens_kwargs: dict) -> None:
        sens_mat = [
            [X1TDISens, XY1TDISens, ZX1TDISens],
            [XY1TDISens, Y1TDISens, YZ1TDISens],
            [ZX1TDISens, YZ1TDISens, Z1TDISens],
        ]
        super().__init__(f, sens_mat, **sens_kwargs)


class AET1SensitivityMatrix(SensitivityMatrix):
    def __init__(self, f: np.ndarray, **sens_kwargs: dict) -> None:
        sens_mat = [A1TDISens, E1TDISens, T1TDISens]
        super().__init__(f, sens_mat, **sens_kwargs)


class AE1SensitivityMatrix(SensitivityMatrix):
    def __init__(self, f: np.ndarray, **sens_kwargs: dict) -> None:
        sens_mat = [A1TDISens, E1TDISens]
        super().__init__(f, sens_mat, **sens_kwargs)


def get_sensitivity(
    f: float | np.ndarray,
    *args: tuple,
    sens_fn: Optional[Sensitivity | str] = LISASens,
    return_type="PSD",
    **kwargs,
) -> float | np.ndarray:
    """Generic sensitivity generator

    Same interface to many sensitivity curves.

    Args:
        f: Frequency array.
        sens_fn: String or class that represents the name of the desired PSD function.
        *args: Any additional arguments for the sensitivity function ``get_Sn`` method.
        return_type: Described the desired output. Choices are ASD,
            PSD, or char_strain (characteristic strain). Default is ASD.
        **kwargs: Keyword arguments to pass to sensitivity function ``get_Sn`` method.

    Return:
        Sensitivity values.

    """

    if isinstance(sens_fn, str):
        try:
            sensitivity = globals()[sens_fn]
        except KeyError:
            raise ValueError(
                f"{sens_fn} sensitivity is not available. Available stock sensitivities are"
            )

    elif hasattr(sens_fn, "get_Sn"):
        sensitivity = sens_fn

    else:
        raise ValueError(
            "sens_fn must be a string for a stock option or a class with a get_Sn method."
        )

    PSD = sensitivity.get_Sn(f, *args, **kwargs)

    if return_type == "PSD":
        return PSD

    elif return_type == "ASD":
        return PSD ** (1 / 2)

    elif return_type == "char_strain":
        return (f * PSD) ** (1 / 2)

    else:
        raise ValueError("return_type must be PSD, ASD, or char_strain.")


__stock_sens_options__ = [
    "X1TDISens",
    "Y1TDISens",
    "Z1TDISens",
    "XY1TDISens",
    "YZ1TDISens",
    "ZX1TDISens",
    "A1TDISens",
    "E1TDISens",
    "T1TDISens",
    "X2TDISens",
    "Y2TDISens",
    "Z2TDISens",
    "LISASens",
    "CornishLISASens",
    "FlatPSDFunction",
]


def get_stock_sensitivity_options() -> List[Sensitivity]:
    """Get stock options for sensitivity curves.

    Returns:
        List of stock sensitivity options.

    """
    return __stock_sens_options__
