from numbers import Real, Integral

import numpy as np

from . import _batoid
from .constants import globalCoordSys, vacuum
from .coordSys import CoordSys
from .coordTransform import CoordTransform
from .trace import applyForwardTransform
from .utils import lazy_property, fieldToDirCos
from .surface import Plane


def _reshape_arrays(arrays, shape, dtype=float):
    for i in range(len(arrays)):
        array = arrays[i]
        if not hasattr(array, 'shape') or array.shape != shape:
            arrays[i] = np.array(np.broadcast_to(array, shape))
        arrays[i] = np.ascontiguousarray(arrays[i], dtype=dtype)
    return arrays

class RayVector:
    def __init__(
        self, x, y, z, vx, vy, vz, t=0, wavelength=500e-9, flux=1,
        vignetted=False, failed=False, coordSys=globalCoordSys
    ):
        """Create RayVector from 1d parameter arrays.  Always makes a copy
        of input arrays.

        Parameters
        ----------
        x, y, z : ndarray of float, shape (n,)
            Positions of rays in meters.
        vx, vy, vz : ndarray of float, shape (n,)
            Velocities of rays in units of the speed of light in vacuum.
        t : ndarray of float, shape (n,)
            Reference times (divided by the speed of light in vacuum) in units
            of meters.
        wavelength : ndarray of float, shape (n,)
            Vacuum wavelengths in meters.
        flux : ndarray of float, shape (n,)
            Fluxes in arbitrary units.
        vignetted : ndarray of bool, shape (n,)
            True where rays have been vignetted.
        coordSys : CoordSys
            Coordinate system in which this ray is expressed.  Default: the
            global coordinate system.
        """

        shape = np.broadcast(
            x, y, z, vx, vy, vz, t, wavelength, flux, vignetted, failed
        ).shape
        x, y, z, vx, vy, vz, t, wavelength, flux = _reshape_arrays(
            [x, y, z, vx, vy, vz, t, wavelength, flux],
            shape
        )
        vignetted, failed = _reshape_arrays(
            [vignetted, failed],
            shape,
            bool
        )

        self._r = np.ascontiguousarray([x, y, z], dtype=float).T
        self._v = np.ascontiguousarray([vx, vy, vz], dtype=float).T
        self._t = t
        self._wavelength = wavelength
        self._flux = flux
        self._vignetted = vignetted
        self._failed = failed

        self.coordSys = coordSys

    def positionAtTime(self, t):
        """Calculate the positions of the rays at a given time.

        Parameters
        ----------
        t : float
            Time (over vacuum speed of light; in meters).

        Returns
        -------
        ndarray of float, shape (n, 3)
            Positions in meters.
        """
        out = np.empty_like(self._r)
        self._rv.positionAtTime(t, out.ctypes.data)
        return out

    def propagate(self, t):
        """Propagate this RayVector to given time.

        Parameters
        ----------
        t : float
            Time (over vacuum speed of light; in meters).

        Returns
        -------
        RayVector
            Reference to self, no copy is made.
        """
        self._rv.propagateInPlace(t)

    def phase(self, r, t):
        """Calculate plane wave phases at given position and time.

        Parameters
        ----------
        r : ndarray of float, shape (3,)
            Position in meters at which to compute phase
        t : float
            Time (over vacuum speed of light; in meters).

        Returns
        -------
        ndarray of float, shape(n,)
        """
        out = np.empty_like(self._t)
        self._rv.phase(r[0], r[1], r[2], t, out.ctypes.data)
        return out

    def amplitude(self, r, t):
        """Calculate (scalar) complex electric-field amplitudes at given
        position and time.

        Parameters
        ----------
        r : ndarray of float, shape (3,)
            Position in meters.
        t : float
            Time (over vacuum speed of light; in meters).

        Returns
        -------
        ndarray of complex, shape (n,)
        """
        out = np.empty_like(self._t, dtype=np.complex128)
        self._rv.amplitude(r[0], r[1], r[2], t, out.ctypes.data)
        return out

    def sumAmplitude(self, r, t, ignoreVignetted=True):
        """Calculate the sum of (scalar) complex electric-field amplitudes of
        all rays at given position and time.

        Parameters
        ----------
        r : ndarray of float, shape (3,)
            Position in meters.
        t : float
            Time (over vacuum speed of light; in meters).

        Returns
        -------
        complex
        """
        return self._rv.sumAmplitude(r[0], r[1], r[2], t, ignoreVignetted)

    @classmethod
    def asGrid(
        cls,
        optic=None, backDist=None, medium=None, stopSurface=None,
        wavelength=None,
        source=None, dirCos=None,
        theta_x=None, theta_y=None, projection='postel',
        nx=None, ny=None,
        dx=None, dy=None,
        lx=None, ly=None,
        flux=1,
        nrandom=None
    ):
        """Create RayVector on a parallelogram shaped region.

        This function will often be used to create a grid of rays on a square
        grid, but is flexible enough to also create gris on an arbitrary
        parallelogram, or even randomly distributed across an arbitrary
        parallelogram-shaped region.

        The algorithm starts by placing rays on the "stop" surface, and then
        backing them up such that they are in front of any surfaces of the
        optic they're intended to trace.

        The stop surface of most large telescopes is the plane perpendicular to
        the optic axis and flush with the rim of the primary mirror.  This
        plane is usually also the entrance pupil since there are no earlier
        refractive or reflective surfaces.  However, since this plane is a bit
        difficult to locate automatically, the default stop surface in batoid
        is the global x-y plane.

        If a telescope has an stopSurface attribute in its yaml file, then this
        is usually a good choice to use in this function.  Using a curved
        surface for the stop surface is allowed, but is usually a bad idea as
        this may lead to a non-uniformly illuminated pupil and is inconsistent
        with, say, an incoming uniform spherical wave or uniform plane wave.

        Parameters
        ----------
        optic : `batoid.Optic`, optional
            If present, then try to extract values for ``backDist``,
            ``medium``, ``stopSurface``, and ``lx`` from the Optic.  Note that
            values explicitly passed to `asGrid` as keyword arguments override
            those extracted from ``optic``.
        backDist : float, optional
            Map rays backwards from the stop surface to the plane that is
            perpendicular to the ray and ``backDist`` meters from the point
            (0, 0, z(0,0)) on the stop surface.  This should generally be set
            large enough that any obscurations or phantom surfaces occuring
            before the stop surface are now "in front" of the ray.  If this
            keyword is set to ``None`` and the ``optic`` keyword is set, then
            infer a value from ``optic.backDist``.  If both this keyword and
            ``optic`` are ``None``, then use a default of 40 meters, which
            should be sufficiently large for foreseeable telescopes.
        medium : `batoid.Medium`, optional
            Initial medium of each ray.  If this keyword is set to ``None`` and
            the ``optic`` keyword is set, then infer a value from
            ``optic.inMedium``.  If both this keyword and ``optic`` are
            ``None``, then use a default of vacuum.
        stopSurface : batoid.Interface, optional
            Surface defining the system stop.  If this keyword is set to
            ``None`` and the ``optic`` keyword is set, then infer a value from
            ``optic.stopSurface``.  If both this keyword and ``optic`` are
            ``None``, then use a default ``Interface(Plane())``, which is the
            global x-y plane.
        wavelength : float
            Vacuum wavelength of rays in meters.
        source : None or ndarray of float, shape (3,), optional
            Where rays originate.  If None, then rays originate an infinite
            distance away, in which case the ``dirCos`` kwarg must also be
            specified to set the direction of ray propagation.  If an ndarray,
            then the rays originate from this point in global coordinates and
            the ``dirCos`` kwarg is ignored.
        dirCos : ndarray of float, shape (3,), optional
            If source is None, then this indicates the initial direction of
            propagation of the rays.  If source is not None, then this is
            ignored.  Also see ``theta_x``, ``theta_y`` as an alternative to
            this keyword.
        theta_x, theta_y : float, optional
            Field angle in radians.  If source is None, then this indicates the
            initial direction of propagation of the rays.  If source is not
            None, then this is ignored.  Uses `utils.fieldToDirCos` to convert
            to direction cosines.  Also see ``dirCos`` as an alternative to
            this keyword.
        projection : {'postel', 'zemax', 'gnomonic', 'stereographic', 'lambert', 'orthographic'}, optional
            Projection used to convert field angle to direction cosines.
        nx, ny : int, optional
            Number of rays on each side of grid.
        dx, dy : float or (2,) array of float, optional
            Separation in meters between adjacent rays in grid.  If scalars,
            then the separations are exactly along the x and y directions.  If
            arrays, then these are interpretted as the primitive vectors for
            the first and second dimensions of the grid.  If only dx is
            explicitly specified, then dy will be inferred as a 90-degree
            rotation from dx with the same length as dx.
        lx, ly : float or (2,) array of float, optional
            Length of each side of ray grid.  If scalars, then these are
            measured along the x and y directions.  If arrays, then these also
            indicate the primitive vectors orientation of the grid.  If only
            lx is specified, then ly will be inferred as a 90-degree rotation
            from lx with the same length as lx.  If lx is ``None``, then first
            infer a value from ``nx`` and ``dx``, and if that doesn't work,
            infer a value from ``optic.pupilSize``.
        flux : float, optional
            Flux to assign each ray.  Default is 1.0.
        nrandom : None or int, optional
            If not None, then uniformly sample this many rays from
            parallelogram region instead of sampling on a regular grid.
        """
        from .optic import Interface
        from .surface import Plane

        if optic is not None:
            if backDist is None:
                backDist = optic.backDist
            if medium is None:
                medium = optic.inMedium
            if stopSurface is None:
                try:
                    stopSurface = optic.stopSurface
                except AttributeError:
                    stopSurface = None
            if lx is None:
                # If nx and dx are both present, then let lx get inferred from
                # them.  Otherwise, infer from optic.
                if nx is None or dx is None:
                    lx = optic.pupilSize

        if backDist is None:
            backDist = 40.0
        if stopSurface is None:
            stopSurface = Interface(Plane())
        if medium is None:
            medium = vacuum

        if dirCos is None and source is None:
            dirCos = fieldToDirCos(theta_x, theta_y, projection=projection)

        if wavelength is None:
            raise ValueError("Missing wavelength keyword")

        # To determine the parallelogram, exactly 2 of nx, dx, lx must be set.
        if sum(a is not None for a in [nx, dx, lx]) != 2:
            raise ValueError("Exactly 2 of nx, dx, lx must be specified")

        if nx is not None and ny is None:
            ny = nx
        if dx is not None and dy is None:
            dy = dx
        if lx is not None and ly is None:
            if isinstance(lx, Real):
                ly = lx
            else:
                ly = np.dot(np.array([[0, -1], [1, 0]]), lx)

        # We need lx, ly, nx, ny for below, so construct these from other
        # arguments if they're not already available.
        if nx is not None and dx is not None:
            if (nx%2) == 0:
                lx = dx*(nx-2)
            else:
                lx = dx*(nx-1)
            if (ny%2) == 0:
                ly = dy*(ny-2)
            else:
                ly = dy*(ny-1)
        elif lx is not None and dx is not None:
            # adjust dx in this case
            # always infer an even n (since even and odd are degenerate given
            # only lx, dx).
            slop = 0.1  # prevent 3.9999 -> 3, e.g.
            nx = int((lx/dx+slop)//2)*2+2
            ny = int((ly/dy+slop)//2)*2+2
            # These are the real dx, dy; which may be different from what was
            # passed in order to force an integer for nx/ny.  We don't actually
            # need them after this point though.
            # dx = lx/(nx-2)
            # dy = ly/(ny-2)

        if isinstance(lx, Real):
            lx = (lx, 0.0)
        if isinstance(ly, Real):
            ly = (0.0, ly)

        if nrandom is not None:
            x = np.random.uniform(-0.5, 0.5, size=nrandom)
            y = np.random.uniform(-0.5, 0.5, size=nrandom)
        else:
            if nx <= 2:
                x_d = 1.
            else:
                x_d = (nx-(2 if (nx%2) == 0 else 1))/nx
            if ny <= 2:
                y_d = 1.
            else:
                y_d = (ny-(2 if (ny%2) == 0 else 1))/ny
            x = np.fft.fftshift(np.fft.fftfreq(nx, x_d))
            y = np.fft.fftshift(np.fft.fftfreq(ny, y_d))
            x, y = np.meshgrid(x, y)
            x = x.ravel()
            y = y.ravel()
        stack = np.stack([x, y])
        x = np.dot(lx, stack)
        y = np.dot(ly, stack)
        z = stopSurface.surface.sag(x, y)
        transform = CoordTransform(stopSurface.coordSys, globalCoordSys)
        x, y, z = transform.applyForwardArray(x, y, z)

        w = np.empty_like(x)
        w.fill(wavelength)
        n = medium.getN(wavelength)

        return cls._finish(backDist, source, dirCos, n, x, y, z, w, flux)

    @classmethod
    def asPolar(
        cls,
        optic=None, backDist=None, medium=None, stopSurface=None,
        wavelength=None,
        outer=None, inner=0.0,
        source=None, dirCos=None,
        theta_x=None, theta_y=None, projection='postel',
        nrad=None, naz=None,
        flux=1,
        nrandom=None
    ):
        """Create RayVector on an annular region using a hexapolar grid.

        This function can be used to regularly sample the entrance pupil of a
        telescope using polar symmetry (really, hexagonal symmetry).  Rings of
        different radii are used, with the number of samples on each ring
        restricted to a multiple of 6 (with the exception of a potential
        central "ring" of radius 0, which is only ever sampled once).  This may
        be more efficient than using a square grid since more of the rays
        generated may avoid vignetting.

        This function is also used to generate rays uniformly randomly sampled
        from a given annular region.

        The algorithm used here starts by placing rays on the "stop" surface,
        and then backing them up such that they are in front of any surfaces of
        the optic they're intended to trace.

        The stop surface of most large telescopes is the plane perpendicular to
        the optic axis and flush with the rim of the primary mirror.  This
        plane is usually also the entrance pupil since there are no earlier
        refractive or reflective surfaces.  However, since this plane is a bit
        difficult to locate automatically, the default stop surface in batoid
        is the global x-y plane.

        If a telescope has an stopSurface attribute in its yaml file, then this
        is usually a good choice to use in this function.  Using a curved
        surface for the stop surface is allowed, but is usually a bad idea as
        this may lead to a non-uniformly illuminated pupil and is inconsistent
        with, say, an incoming uniform spherical wave or uniform plane wave.

        Parameters
        ----------
        optic : `batoid.Optic`, optional
            If present, then try to extract values for ``backDist``,
            ``medium``, ``stopSurface``, and ``outer`` from the Optic.  Note
            that values explicitly passed to `asPolar` as keyword arguments
            override those extracted from ``optic``.
        backDist : float, optional
            Map rays backwards from the stop surface to the plane that is
            perpendicular to the ray and ``backDist`` meters from the point
            (0, 0, z(0,0)) on the stop surface.  This should generally be set
            large enough that any obscurations or phantom surfaces occuring
            before the stop surface are now "in front" of the ray.  If this
            keyword is set to ``None`` and the ``optic`` keyword is set, then
            infer a value from ``optic.backDist``.  If both this keyword and
            ``optic`` are ``None``, then use a default of 40 meters, which
            should be sufficiently large for foreseeable telescopes.
        medium : `batoid.Medium`, optional
            Initial medium of each ray.  If this keyword is set to ``None`` and
            the ``optic`` keyword is set, then infer a value from
            ``optic.inMedium``.  If both this keyword and ``optic`` are
            ``None``, then use a default of vacuum.
        stopSurface : batoid.Interface, optional
            Surface defining the system stop.  If this keyword is set to
            ``None`` and the ``optic`` keyword is set, then infer a value from
            ``optic.stopSurface``.  If both this keyword and ``optic`` are
            ``None``, then use a default ``Interface(Plane())``, which is the
            global x-y plane.
        wavelength : float
            Vacuum wavelength of rays in meters.
        outer : float
            Outer radius of annulus in meters.
        inner : float, optional
            Inner radius of annulus in meters.  Default is 0.0.
        source : None or ndarray of float, shape (3,), optional
            Where rays originate.  If None, then rays originate an infinite
            distance away, in which case the ``dirCos`` kwarg must also be
            specified to set the direction of ray propagation.  If an ndarray,
            then the rays originate from this point in global coordinates and
            the ``dirCos`` kwarg is ignored.
        dirCos : ndarray of float, shape (3,), optional
            If source is None, then this indicates the initial direction of
            propagation of the rays.  If source is not None, then this is
            ignored.  Also see ``theta_x``, ``theta_y`` as an alternative to
            this keyword.
        theta_x, theta_y : float, optional
            Field angle in radians.  If source is None, then this indicates the
            initial direction of propagation of the rays.  If source is not
            None, then this is ignored.  Uses `utils.fieldToDirCos` to convert
            to direction cosines.  Also see ``dirCos`` as an alternative to
            this keyword.
        projection : {'postel', 'zemax', 'gnomonic', 'stereographic', 'lambert', 'orthographic'}, optional
            Projection used to convert field angle to direction cosines.
        nrad : int
            Number of radii on which create rays.
        naz : int
            Approximate number of azimuthal angles uniformly spaced along the
            outermost ring.  Each ring is constrained to have a multiple of 6
            azimuths, so the realized value may be slightly different than
            the input value here.  Inner rings will have fewer azimuths in
            proportion to their radius, but will still be constrained to a
            multiple of 6.  (If the innermost ring has radius 0, then exactly
            1 ray, with azimuth undefined, will be used on that "ring".)
        flux : float, optional
            Flux to assign each ray.  Default is 1.0.
        nrandom : int, optional
            If not None, then uniformly sample this many rays from annular
            region instead of sampling on a hexapolar grid.
        """
        from .optic import Interface

        if optic is not None:
            if backDist is None:
                backDist = optic.backDist
            if medium is None:
                medium = optic.inMedium
            if stopSurface is None:
                stopSurface = optic.stopSurface
            if outer is None:
                outer = optic.pupilSize/2

        if backDist is None:
            backDist = 40.0
        if stopSurface is None:
            stopSurface = Interface(Plane())
        if medium is None:
            medium = vacuum

        if dirCos is None and source is None:
            dirCos = fieldToDirCos(theta_x, theta_y, projection=projection)

        if wavelength is None:
            raise ValueError("Missing wavelength keyword")

        if nrandom is None:
            ths = []
            rs = []
            for r in np.linspace(outer, inner, nrad):
                if r == 0:
                    break
                nphi = int((naz*r/outer)//6)*6
                if nphi == 0:
                    nphi = 6
                ths.append(np.linspace(0, 2*np.pi, nphi, endpoint=False))
                rs.append(np.ones(nphi)*r)
            # Point in center is a special case
            if inner == 0.0:
                ths[-1] = np.array([0.0])
                rs[-1] = np.array([0.0])
            r = np.concatenate(rs)
            th = np.concatenate(ths)
        else:
            r = np.sqrt(np.random.uniform(inner**2, outer**2, size=nrandom))
            th = np.random.uniform(0, 2*np.pi, size=nrandom)
        x = r*np.cos(th)
        y = r*np.sin(th)
        z = stopSurface.surface.sag(x, y)
        transform = CoordTransform(stopSurface.coordSys, globalCoordSys)
        x, y, z = transform.applyForwardArray(x, y, z)
        w = np.empty_like(x)
        w.fill(wavelength)
        n = medium.getN(wavelength)

        return cls._finish(backDist, source, dirCos, n, x, y, z, w, flux)

    @classmethod
    def asSpokes(
        cls,
        optic=None, backDist=None, medium=None, stopSurface=None,
        wavelength=None,
        outer=None, inner=0.0,
        source=None, dirCos=None,
        theta_x=None, theta_y=None, projection='postel',
        spokes=None, rings=None,
        spacing='uniform',
        flux=1
    ):
        """Create RayVector on an annular region using a spokes pattern.

        The function generates rays on a rings-and-spokes pattern, with a fixed
        number of radii for each azimuth and a fixed number of azimuths for
        each radius.  Its main use is for decomposing functions in pupil space
        into Zernike components using Gaussian Quadrature integration on
        annuli.  For more general purpose annular sampling, RayVector.asPolar()
        is often a better choice since it samples the pupil more uniformly.

        The algorithm used here starts by placing rays on the "stop" surface,
        and then backing them up such that they are in front of any surfaces of
        the optic they're intended to trace.

        The stop surface of most large telescopes is the plane perpendicular to
        the optic axis and flush with the rim of the primary mirror.  This
        plane is usually also the entrance pupil since there are no earlier
        refractive or reflective surfaces.  However, since this plane is a bit
        difficult to locate automatically, the default stop surface in batoid
        is the global x-y plane.

        If a telescope has an stopSurface attribute in its yaml file, then this
        is usually a good choice to use in this function.  Using a curved
        surface for the stop surface is allowed, but is usually a bad idea as
        this may lead to a non-uniformly illuminated pupil and is inconsistent
        with, say, an incoming uniform spherical wave or uniform plane wave.

        Parameters
        ----------
        optic : `batoid.Optic`, optional
            If present, then try to extract values for ``backDist``,
            ``medium``, ``stopSurface``, and ``outer`` from the Optic.  Note
            that values explicitly passed to `asSpokes` as keyword arguments
            override those extracted from ``optic``.
        backDist : float, optional
            Map rays backwards from the stop surface to the plane that is
            perpendicular to the ray and ``backDist`` meters from the point
            (0, 0, z(0,0)) on the stop surface.  This should generally be set
            large enough that any obscurations or phantom surfaces occuring
            before the stop surface are now "in front" of the ray.  If this
            keyword is set to ``None`` and the ``optic`` keyword is set, then
            infer a value from ``optic.backDist``.  If both this keyword and
            ``optic`` are ``None``, then use a default of 40 meters, which
            should be sufficiently large for foreseeable telescopes.
        medium : `batoid.Medium`, optional
            Initial medium of each ray.  If this keyword is set to ``None`` and
            the ``optic`` keyword is set, then infer a value from
            ``optic.inMedium``.  If both this keyword and ``optic`` are
            ``None``, then use a default of vacuum.
        stopSurface : batoid.Interface, optional
            Surface defining the system stop.  If this keyword is set to
            ``None`` and the ``optic`` keyword is set, then infer a value from
            ``optic.stopSurface``.  If both this keyword and ``optic`` are
            ``None``, then use a default ``Interface(Plane())``, which is the
            global x-y plane.
        wavelength : float
            Vacuum wavelength of rays in meters.
        outer : float
            Outer radius of annulus in meters.
        inner : float, optional
            Inner radius of annulus in meters.  Default is 0.0.
        source : None or ndarray of float, shape (3,), optional
            Where rays originate.  If None, then rays originate an infinite
            distance away, in which case the ``dirCos`` kwarg must also be
            specified to set the direction of ray propagation.  If an ndarray,
            then the rays originate from this point in global coordinates and
            the ``dirCos`` kwarg is ignored.
        dirCos : ndarray of float, shape (3,), optional
            If source is None, then this indicates the initial direction of
            propagation of the rays.  If source is not None, then this is
            ignored.  Also see ``theta_x``, ``theta_y`` as an alternative to
            this keyword.
        theta_x, theta_y : float, optional
            Field angle in radians.  If source is None, then this indicates the
            initial direction of propagation of the rays.  If source is not
            None, then this is ignored.  Uses `utils.fieldToDirCos` to convert
            to direction cosines.  Also see ``dirCos`` as an alternative to
            this keyword.
        projection : {'postel', 'zemax', 'gnomonic', 'stereographic', 'lambert', 'orthographic'}, optional
            Projection used to convert field angle to direction cosines.
        spokes : int or ndarray of float
            If int, then number of spokes to use.
            If ndarray, then the values of the spokes azimuthal angles in
            radians.
        rings : int or ndarray of float
            If int, then number of rings to use.
            If array, then the values of the ring radii to use in meters.
        spacing : {'uniform', 'GQ'}
            If uniform, assign ring radii uniformly between ``inner`` and
            ``outer``.
            If GQ, then assign ring radii as the Gaussian Quadrature points
            for integration on an annulus.  In this case, the ray fluxes will
            be set to the Gaussian Quadrature weights (and the ``flux`` kwarg
            will be ignored).
        flux : float, optional
            Flux to assign each ray.  Default is 1.0.
        """
        from .optic import Interface
        from .surface import Plane

        if optic is not None:
            if backDist is None:
                backDist = optic.backDist
            if medium is None:
                medium = optic.inMedium
            if stopSurface is None:
                stopSurface = optic.stopSurface
            if outer is None:
                outer = optic.pupilSize/2

        if backDist is None:
            backDist = 40.0
        if stopSurface is None:
            stopSurface = Interface(Plane())
        if medium is None:
            medium = vacuum

        if dirCos is None and source is None:
            dirCos = fieldToDirCos(theta_x, theta_y, projection=projection)

        if wavelength is None:
            raise ValueError("Missing wavelength keyword")

        if isinstance(rings, Integral):
            if spacing == 'uniform':
                rings = np.linspace(inner, outer, rings)
            elif spacing == 'GQ':
                if spokes is None:
                    spokes = 2*rings+1
                Li, w = np.polynomial.legendre.leggauss(rings)
                eps = inner/outer
                area = np.pi*(1-eps**2)
                rings = np.sqrt(eps**2 + (1+Li)*(1-eps**2)/2)*outer
                flux = w*area/(2*spokes)
            if isinstance(spokes, Integral):
                spokes = np.linspace(0, 2*np.pi, spokes, endpoint=False)
        rings, spokes = np.meshgrid(rings, spokes)
        flux = np.broadcast_to(flux, rings.shape)
        rings = rings.ravel()
        spokes = spokes.ravel()
        flux = flux.ravel()

        x = rings*np.cos(spokes)
        y = rings*np.sin(spokes)
        z = stopSurface.surface.sag(x, y)
        transform = CoordTransform(stopSurface.coordSys, globalCoordSys)
        x, y, z = transform.applyForwardArray(x, y, z)
        w = np.empty_like(x)
        w.fill(wavelength)
        n = medium.getN(wavelength)

        return cls._finish(backDist, source, dirCos, n, x, y, z, w, flux)

    @classmethod
    def _finish(cls, backDist, source, dirCos, n, x, y, z, w, flux):
        """Map rays backwards to their source position."""
        if source is None:
            v = np.array(dirCos, dtype=float)
            v /= n*np.sqrt(np.dot(v, v))
            vx = np.empty_like(x)
            vx.fill(v[0])
            vy = np.empty_like(x)
            vy.fill(v[1])
            vz = np.empty_like(x)
            vz.fill(v[2])
            # Now need to raytrace backwards to the plane dist units away.
            t = 0
            rays = RayVector(x, y, z, -vx, -vy, -vz, t, w, flux=flux)

            zhat = -n*v
            xhat = np.cross(np.array([1.0, 0.0, 0.0]), zhat)
            xhat /= np.sqrt(np.dot(xhat, xhat))
            yhat = np.cross(xhat, zhat)
            origin = zhat*backDist
            cs = CoordSys(origin, np.stack([xhat, yhat, zhat]).T)
            transform = CoordTransform(globalCoordSys, cs)
            transform.applyForward(rays)
            plane = Plane()
            plane.intersect(rays)
            transform.applyReverse(rays)
            t = 0
            return RayVector(
                rays.x, rays.y, rays.z, vx, vy, vz, t, w, flux=flux
            )
        else:
            vx = x - source[0]
            vy = y - source[1]
            vz = z - source[2]
            v = np.stack([vx, vy, vz])
            v /= n*np.einsum('ab,ab->b', v, v)
            x.fill(source[0])
            y.fill(source[1])
            z.fill(source[2])
            t = 0
            return RayVector(x, y, z, v[0], v[1], v[2], t, w, flux=flux)

    @classmethod
    def fromStop(
        cls, x, y,
        optic=None, backDist=None, medium=None, stopSurface=None,
        wavelength=None,
        source=None, dirCos=None,
        theta_x=None, theta_y=None, projection='postel',
        flux=1, coordSys=globalCoordSys
    ):
        """Create rays that intersects the "stop" surface at given points.

        The algorithm used here starts by placing the rays on the "stop"
        surface, and then backing them up such that they are in front of any
        surfaces of the optic they're intended to trace.

        The stop surface of most large telescopes is the plane perpendicular to
        the optic axis and flush with the rim of the primary mirror.  This
        plane is usually also the entrance pupil since there are no earlier
        refractive or reflective surfaces.  However, since this plane is a bit
        difficult to locate automatically, the default stop surface in batoid
        is the global x-y plane.

        If a telescope has an stopSurface attribute in its yaml file, then this
        is usually a good choice to use in this function.  Using a curved
        surface for the stop surface is allowed, but is usually a bad idea as
        this may lead to a non-uniformly illuminated pupil and is inconsistent
        with, say, an incoming uniform spherical wave or uniform plane wave.

        Parameters
        ----------
        x, y : ndarray
            X/Y coordinates on the stop surface where the rays would intersect
            if not refracted or reflected first.
        optic : `batoid.Optic`, optional
            If present, then try to extract values for ``backDist``,
            ``medium``, and ``stopSurface`` from the Optic.  Note that values
            explicitly passed here as keyword arguments override those
            extracted from ``optic``.
        backDist : float, optional
            Map rays backwards from the stop surface to the plane that is
            perpendicular to the rays and ``backDist`` meters from the point
            (0, 0, z(0,0)) on the stop surface.  This should generally be set
            large enough that any obscurations or phantom surfaces occuring
            before the stop surface are now "in front" of the ray.  If this
            keyword is set to ``None`` and the ``optic`` keyword is set, then
            infer a value from ``optic.backDist``.  If both this keyword and
            ``optic`` are ``None``, then use a default of 40 meters, which
            should be sufficiently large for foreseeable telescopes.
        medium : `batoid.Medium`, optional
            Initial medium of rays.  If this keyword is set to ``None`` and
            the ``optic`` keyword is set, then infer a value from
            ``optic.inMedium``.  If both this keyword and ``optic`` are
            ``None``, then use a default of vacuum.
        stopSurface : batoid.Interface, optional
            Surface defining the system stop.  If this keyword is set to
            ``None`` and the ``optic`` keyword is set, then infer a value from
            ``optic.stopSurface``.  If both this keyword and ``optic`` are
            ``None``, then use a default ``Interface(Plane())``, which is the
            global x-y plane.
        wavelength : float
            Vacuum wavelength of rays in meters.
        source : None or ndarray of float, shape (3,), optional
            Where the rays originate.  If None, then the rays originate an
            infinite distance away, in which case the ``dirCos`` kwarg must also
            be specified to set the direction of ray propagation.  If an
            ndarray, then the rays originates from this point in global
            coordinates and the ``dirCos`` kwarg is ignored.
        dirCos : ndarray of float, shape (3,), optional
            If source is None, then indicates the direction of ray propagation.
            If source is not None, then this is ignored.
        theta_x, theta_y : float, optional
            Field angle in radians.  If source is None, then this indicates the
            initial direction of propagation of the rays.  If source is not
            None, then this is ignored.  Uses `utils.fieldToDirCos` to convert
            to direction cosines.  Also see ``dirCos`` as an alternative to
            this keyword.
        projection : {'postel', 'zemax', 'gnomonic', 'stereographic', 'lambert', 'orthographic'}, optional
            Projection used to convert field angle to direction cosines.
        flux : float, optional
            Flux of rays.  Default is 1.0.
        coordSys : CoordSys
            Coordinate system in which rays are expressed.  Default: the global
            coordinate system.
        """
        from .optic import Interface
        from .surface import Plane

        if optic is not None:
            if backDist is None:
                backDist = optic.backDist
            if medium is None:
                medium = optic.inMedium
            if stopSurface is None:
                stopSurface = optic.stopSurface

        if backDist is None:
            backDist = 40.0
        if stopSurface is None:
            stopSurface = Interface(Plane())
        if medium is None:
            medium = vacuum

        if dirCos is None and source is None:
            dirCos = fieldToDirCos(theta_x, theta_y, projection=projection)

        if wavelength is None:
            raise ValueError("Missing wavelength keyword")

        z = stopSurface.surface.sag(x, y)
        transform = CoordTransform(stopSurface.coordSys, globalCoordSys)
        x, y, z = transform.applyForwardArray(x, y, z)

        w = np.empty_like(x)
        w.fill(wavelength)
        n = medium.getN(wavelength)

        return cls._finish(backDist, source, dirCos, n, x, y, z, w, flux)

    @property
    def r(self):
        """ndarray of float, shape (n, 3): Positions of rays in meters."""
        self._rv.r.syncToHost()
        return self._r

    @property
    def x(self):
        """The x components of ray positions in meters."""
        self._rv.r.syncToHost()
        return self._r[:, 0]

    @property
    def y(self):
        """The y components of ray positions in meters."""
        self._rv.r.syncToHost()
        return self._r[:, 1]

    @property
    def z(self):
        """The z components of ray positions in meters."""
        self._rv.r.syncToHost()
        return self._r[:, 2]

    @property
    def v(self):
        """ndarray of float, shape (n, 3): Velocities of rays in units of the
        speed of light in vacuum.  Note that these may have magnitudes < 1 if
        the rays are inside a refractive medium.
        """
        self._rv.v.syncToHost()
        return self._v

    @property
    def vx(self):
        """The x components of ray velocities units of the vacuum speed of
        light.
        """
        self._rv.v.syncToHost()
        return self._v[:, 0]

    @property
    def vy(self):
        """The y components of ray velocities units of the vacuum speed of
        light.
        """
        self._rv.v.syncToHost()
        return self._v[:, 1]

    @property
    def vz(self):
        """The z components of ray velocities units of the vacuum speed of
        light.
        """
        self._rv.v.syncToHost()
        return self._v[:, 2]

    @property
    def t(self):
        """Reference times (divided by the speed of light in vacuum) in units
        of meters, also known as the optical path lengths.
        """
        self._rv.t.syncToHost()
        return self._t

    @property
    def wavelength(self):
        """Vacuum wavelengths in meters."""
        # wavelength is constant, so no need to synchronize
        return self._wavelength

    @property
    def flux(self):
        """Fluxes in arbitrary units."""
        self._rv.flux.syncToHost()
        return self._flux

    @property
    def vignetted(self):
        """True for rays that have been vignetted."""
        self._rv.vignetted.syncToHost()
        return self._vignetted

    @property
    def failed(self):
        """True for rays that have failed.  This may occur, for example, if
        batoid failed to find the intersection of a ray wiht a surface.
        """
        self._rv.failed.syncToHost()
        return self._failed

    @property
    def k(self):
        r"""ndarray of float, shape (n, 3): Wavevectors of plane waves in units
        of radians per meter.  The magnitude of each wavevector is equal to
        :math:`2 \pi n / \lambda`, where :math:`n` is the refractive index and
        :math:`\lambda` is the wavelength.
        """
        out = 2*np.pi*np.array(self.v)
        out /= self.wavelength[:, None]
        out /= np.sum(self.v*self.v, axis=-1)[:, None]
        return out

    @property
    def kx(self):
        """The x component of each ray wavevector in radians per meter."""
        return self.k[:,0]

    @property
    def ky(self):
        """The y component of each ray wavevector in radians per meter."""
        return self.k[:,1]

    @property
    def kz(self):
        """The z component of each ray wavevector in radians per meter."""
        return self.k[:,2]

    @property
    def omega(self):
        r"""The temporal angular frequency of each plane wave divided by the
        vacuum speed of light in units of radians per meter.  Equals
        :math:`2 \pi / \lambda`.
        """
        return 2*np.pi/self.wavelength

    @lazy_property
    def _rv(self):
        return _batoid.CPPRayVector(
            self._r.ctypes.data, self._v.ctypes.data, self._t.ctypes.data,
            self._wavelength.ctypes.data, self._flux.ctypes.data,
            self._vignetted.ctypes.data, self._failed.ctypes.data,
            len(self._wavelength)
        )

    def _syncToHost(self):
        self._rv.r.syncToHost()
        self._rv.v.syncToHost()
        self._rv.t.syncToHost()
        self._rv.wavelength.syncToHost()
        self._rv.flux.syncToHost()
        self._rv.vignetted.syncToHost()
        self._rv.failed.syncToHost()

    def _syncToDevice(self):
        self._rv.r.syncToDevice()
        self._rv.v.syncToDevice()
        self._rv.t.syncToDevice()
        self._rv.wavelength.syncToDevice()
        self._rv.flux.syncToDevice()
        self._rv.vignetted.syncToDevice()
        self._rv.failed.syncToDevice()

    def copy(self):
        # copy on host side for now...
        self._syncToHost()
        x = self._r[:, 0].copy()
        y = self._r[:, 1].copy()
        z = self._r[:, 2].copy()
        vx = self._v[:, 0].copy()
        vy = self._v[:, 1].copy()
        vz = self._v[:, 2].copy()
        t = self._t.copy()
        wavelength = self._wavelength.copy()
        flux = self._flux.copy()
        vignetted = self._vignetted.copy()
        failed = self._failed.copy()

        return RayVector(
            x, y, z,
            vx, vy, vz,
            t, wavelength, flux, vignetted, failed, self.coordSys.copy()
        )

    def toCoordSys(self, coordSys):
        """Transform this RayVector into a new coordinate system.

        Parameters
        ----------
        coordSys: batoid.CoordSys
            Destination coordinate system.

        Returns
        -------
        RayVector
            Reference to self, no copy is made.
        """
        transform = CoordTransform(self.coordSys, coordSys)
        applyForwardTransform(transform, self)
        return self

    def __len__(self):
        return self._rv.t.size

    def __eq__(self, rhs):
        return self._rv == rhs._rv

    def __ne__(self, rhs):
        return self._rv != rhs._rv

    def __repr__(self):
        out = f"RayVector({self.x!r}, {self.y!r}, {self.z!r}"
        out += f", {self.vx!r}, {self.vy!r}, {self.vz!r}"
        out += f", {self.t!r}, {self.wavelength!r}, {self.flux!r}"
        out += f", {self.vignetted!r}, {self.failed!r}, {self.coordSys!r})"
        return out
