import math
from collections import defaultdict

import scipy.constants as cs

from .constants import FINE_STRUCTURE_CONSTANT_MEV_FM, HBAR_MEV_S
from .units import Energy, Power, HalfLife, Distance


class AlphaCalculationMixin(object):

    @classmethod
    def load(cls, components, q_value, **kwargs):
        if components is None:
            return None
        parent, daughters = components
        return cls(parent, daughters, q_value, **kwargs)

    def __init__(self, parent, daughters, q_value, **kwargs):
        self.parent = parent
        self.smaller, self.larger = daughters
        self.q_value = q_value
        self.kwargs = kwargs


class CoulombBarrier(object):

    def __init__(self, n0, n1):
        self.n0 = n0
        self.n1 = n1
        self._base = FINE_STRUCTURE_CONSTANT_MEV_FM * n0.atomic_number * n1.atomic_number

    def height(self, radius):
        height = self._base / float(radius.fermis)
        return Energy.load(mev=height)

    def width(self, q_value):
        width = self._base / q_value.mev
        return Distance.load(fermis=width)


class ReactionEnergy(object):

    def __init__(self, reaction):
        self.reaction = reaction
        self.value = Energy.load(kev=self._kev())

    def _kev(self):
        lvalues = sum(num * i.mass_excess_kev for num, i in self.reaction._lvalues)
        rvalues = sum(num * i.mass_excess_kev for num, i in self.reaction.rvalues)
        return lvalues - rvalues


class GeigerNuttal(AlphaCalculationMixin):

    def value(self):
        return -46.83 + 1.454 * self.larger.atomic_number / math.sqrt(self.q_value.mev)


class Gamow2(AlphaCalculationMixin):
    """Gamow factor for alpha particle tunneling.

    Assumes one of the daughters is a heavy nucleus and the other an alpha particle.
    """

    def value(self):
        Q = self.q_value.mev
        x = Q / CoulombBarrier(self.smaller, self.larger).width(self.q_value).fermis
        t0 = math.sqrt((2 * self.smaller.mass.mev)/(HBAR_MEV_S**2 * Q))
        t1 = self.smaller.atomic_number * self.larger.atomic_number * FINE_STRUCTURE_CONSTANT_MEV_FM
        t2 = math.acos(math.sqrt(x)) - math.sqrt(x * (1 - x))
        return t0 * t1 * t2


class GamowSuppressionFactor(AlphaCalculationMixin):
    """Gamow suppression factor in log10 units

    From Hermes: https://www.lenr-forum.com/forum/index.php/Thread/3434-Document-Isotopic-Composition
    -of-Rossi-Fuel-Sample-Unverified/?postID=29085#post29085.

    Estimate distance between 2 spherical nuclei when they touch
    Estimate distance when supplied energy has overcome Coulomb barrier
    if r >= 1 there is no Coulomb barrier to cross!

    - from Hermes's comments
    """

    def value(self):
        A  = self.larger.mass_number
        Z  = self.larger.atomic_number
        A4 = self.smaller.mass_number
        Z4 = self.smaller.atomic_number
        Q  = self.q_value.mev
        if Q < 0:
            return math.nan
        # Distances in fm
        rs = 1.1 * (pow(A, .333333) + pow(A4, .333333))
        rc = float(Z) * Z4 * 1.43998 / Q
        r  = rs / rc
        G  = 0 if r >= 1 else math.acos(math.sqrt(r)) - math.sqrt(r * (1. - r))
        m  = (float(A) * A4) / (A + A4)
        return 0.2708122 * Z * Z4 * G * math.sqrt(m / Q)


class IsotopicAlphaDecayCalculation(AlphaCalculationMixin):
    """From http://hyperphysics.phy-astr.gsu.edu/hbase/nuclear/alpdec.html
    """

    speed_of_light = 3 * math.pow(10, 8)
    hbarc = 197.33

    def __init__(self, parent, daughters, q_value, **kwargs):
        self.parent = parent
        self.daughters = daughters
        self.q_value = q_value
        self.kwargs = kwargs
        self.parent_isotopic_abundance = parent.isotopic_abundance
        self.screening = kwargs.get('screening') or 0
        self.smaller, self.larger = daughters
        self.A4, self.Z4 = self.smaller.mass_number, self.smaller.atomic_number
        self.A,  self.Z  = self.larger.mass_number,  self.larger.atomic_number
        self.screened_Z = self.Z - self.screening
        self.alpha_mass = self.smaller.mass.mev
        # Ea = Q / (1 + m/M)
        self.alpha_energy = self.q_value.mev / (1 + self.alpha_mass / self.larger.mass.mev)
        # Units in fm
        self.nuclear_separation = 1.2 * (math.pow(self.A4, 1./3) - (-1) * math.pow(self.A, 1./3))

    @property
    def barrier_height(self):
        "Units in MeV"
        return 2 * self.screened_Z * 1.44 / self.nuclear_separation

    @property
    def alpha_velocity(self):
        "Units in m/s"
        return math.sqrt(2 * self.alpha_energy / self.alpha_mass) * self.speed_of_light

    @property
    def barrier_assault_frequency(self):
        "Units in s^-1"
        return self.alpha_velocity * math.pow(10, 15) / (2 * self.nuclear_separation)

    @property
    def gamow_factor(self):
        x = self.alpha_energy / self.barrier_height
        ph = math.sqrt(2 * self.alpha_mass / ((self.hbarc ** 2) * self.alpha_energy))
        return ph * 2 * self.screened_Z * 1.44 * (math.acos(math.sqrt(x)) - math.sqrt(x * (1 - x)))

    @property
    def tunneling_probability(self):
        return math.exp(-2 * self.gamow_factor)

    @property
    def decay_constant(self):
        return self.tunneling_probability * self.barrier_assault_frequency

    @property
    def half_life(self):
        seconds = 0.693 / self.decay_constant
        return HalfLife(seconds, 's')

    def decay(self, **kwargs):
        merged = {**self.kwargs, **kwargs}
        return IsotopicDecay(
            decay_constant=self.decay_constant,
            deposited_energy=self.q_value,
            atomic_number=self.parent.atomic_number,
            isotopic_abundance=self.parent.isotopic_abundance,
            **merged,
        )


class IsotopicDecay(object):

    avogadros_number, _, _ = cs.physical_constants['Avogadro constant']

    def __init__(self, **kwargs):
        self.decay_constant = kwargs['decay_constant']
        self.deposited_energy = kwargs['deposited_energy']
        self.atomic_number = kwargs['atomic_number']
        self.isotopic_abundance = kwargs['isotopic_abundance']
        self.initial = kwargs['moles'] * self.avogadros_number

    def activity(self, **kwargs):
        return self.decay_constant * self.remaining(**kwargs)

    def remaining(self, **kwargs):
        elapsed = kwargs['seconds']
        return self.initial * math.exp(-self.decay_constant * elapsed)

    def power(self, **kwargs):
        watts = self.activity(**kwargs) * self.deposited_energy.joules
        return Power.load(watts=watts)


class AlphaDecay(object):

    @classmethod
    def load(cls, **kwargs):
        copy = kwargs.copy()
        reactions = copy['reactions']
        del copy['reactions']
        return cls(reactions, **copy)

    def __init__(self, reactions, **kwargs):
        self.reactions = list(reactions)
        self.decays = defaultdict(list)
        for d in (r.alpha_decay(**kwargs) for r in self.reactions):
            if d is None:
                continue
            self.decays[d.atomic_number].append(d)
        self.kwargs = kwargs

    def activity(self, **kwargs):
        activity = 0
        for atomic_number, decays in self.decays.items():
            for decay in decays:
                activity += decay.isotopic_abundance * decay.activity(**kwargs)
        return activity

    def power(self, **kwargs):
        return Power.load(watts=1)
