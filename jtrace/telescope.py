import numpy as np
from collections import OrderedDict
import numbers
import jtrace
from .utils import ordered_load


def media_catalog(media_str):
    # This works for LSST, together with jtrace.Air()
    silica = jtrace.SellmeierMedium(
        0.6961663, 0.4079426, 0.8974794,
        0.0684043**2, 0.1162414**2, 9.896161**2)

    # For HSC, we interpolate between values in pdf description
    w = [0.4, 0.6, 0.75, 0.9, 1.1]
    w = np.array(w)*1e-6
    silica_n = [1.47009272, 1.45801158, 1.45421013, 1.45172729, 1.44917721]
    bsl7y_n = [1.53123287, 1.51671428, 1.51225242, 1.50939738, 1.50653251]
    pbl1y_n = [1.57046066, 1.54784671, 1.54157395, 1.53789058, 1.53457169]

    hsc_silica = jtrace.TableMedium(
        jtrace.Table(w, silica_n, jtrace.Table.Interpolant.linear))
    hsc_bsl7y = jtrace.TableMedium(
        jtrace.Table(w, bsl7y_n, jtrace.Table.Interpolant.linear))
    hsc_pbl1y = jtrace.TableMedium(
        jtrace.Table(w, pbl1y_n, jtrace.Table.Interpolant.linear))

    if media_str == 'air':
        return jtrace.Air()
    elif media_str == 'silica':
        return silica
    elif media_str == 'hsc_air':
        return jtrace.ConstMedium(1.0)
    elif media_str == 'hsc_silica':
        return hsc_silica
    elif media_str == 'hsc_bsl7y':
        return hsc_bsl7y
    elif media_str == 'hsc_pbl1y':
        return hsc_pbl1y
    else:
        raise RuntimeError("Unknown medium {}".format(media_str))


class Telescope(object):
    def __init__(self, surfaceList):
        self.surfaces = OrderedDict()
        for surface in surfaceList:
            self.surfaces[surface['name']] = surface

    @classmethod
    def makeFromYAML(cls, infn):
        out = cls.__new__(cls)
        with open(infn, 'r') as infile:
            data = ordered_load(infile)
            # First, parse the optics
            m0 = media_catalog(data.pop('init_medium'))
            surfaces = data.pop('surfaces')
            out.surfaces = OrderedDict()
            for name, sdata in surfaces.items():
                m1 = media_catalog(sdata.pop('medium'))
                sdict = dict(
                    name=name,
                    outer=sdata['outer'],
                    inner=sdata['inner'],
                    type=sdata['surftype'],
                    m0=m0,
                    m1=m1)
                sagtype = sdata['sagtype']
                if sagtype == 'plane':
                    sdict['surface'] = jtrace.Plane(
                        sdata['zvertex'],
                        Rin=sdata['inner'],
                        Rout=sdata['outer'])
                elif sagtype == 'sphere':
                    sdict['surface'] = jtrace.Sphere(
                        sdata['R'],
                        sdata['zvertex'],
                        Rin=sdata['inner'],
                        Rout=sdata['outer'])
                elif sagtype == 'paraboloid':
                    sdict['surface']=jtrace.Paraboloid(
                        sdata['R'],
                        sdata['zvertex'],
                        Rin=sdata['inner'],
                        Rout=sdata['outer'])
                elif sagtype == 'quadric':
                    sdict['surface'] = jtrace.Quadric(
                        sdata['R'],
                        sdata['conic'],
                        sdata['zvertex'],
                        Rin=sdata['inner'],
                        Rout=sdata['outer'])
                elif sagtype == 'asphere':
                    sdict['surface']=jtrace.Asphere(
                        sdata['R'],
                        sdata['conic'],
                        sdata['coef'],
                        sdata['zvertex'],
                        Rin=sdata['inner'],
                        Rout=sdata['outer'])
                else:
                    raise RuntimeError("Unknown surface type {}".format(sagtype))
                out.surfaces[name] = sdict
                m0 = m1
            # Then update any other params in file
            out.__dict__.update(data)
        return out

    def trace(self, r):
        if isinstance(r, jtrace.Ray):
            ray = r
            for name, surface in self.surfaces.items():
                isec = surface['surface'].intersect(ray)
                if surface['type'] == 'mirror':
                    ray = isec.reflectedRay(ray)
                elif surface['type'] in ['lens', 'filter']:
                    ray = isec.refractedRay(ray, surface['m0'], surface['m1'])
                elif surface['type'] == 'det':
                    ray = ray.propagatedToTime(isec.t)
                else:
                    raise ValueError("Unknown optic type: {}".format(surface['type']))
            return ray
        elif isinstance(r, jtrace.RayVector):
            rays = r
            for name, surface in self.surfaces.items():
                isecs = surface['surface'].intersect(rays)
                if surface['type'] == 'mirror':
                    rays = jtrace._jtrace.reflectMany(isecs, rays)
                elif surface['type'] in ['lens', 'filter']:
                    rays = jtrace._jtrace.refractMany(isecs, rays, surface['m0'], surface['m1'])
                elif surface['type'] == 'det':
                    rays = jtrace._jtrace.propagatedToTimesMany(rays, isecs.t)
                else:
                    raise ValueError("Unknown optic type: {}".format(surface['type']))
            return rays

    def traceFull(self, r):
        out = []
        if isinstance(r, jtrace.Ray):
            ray = r
            for name, surface in self.surfaces.items():
                isec = surface['surface'].intersect(ray)
                data = {'name':name, 'inray':ray}
                if surface['type'] == 'mirror':
                    ray = isec.reflectedRay(ray)
                elif surface['type'] in ['lens', 'filter']:
                    ray = isec.refractedRay(ray, surface['m0'], surface['m1'])
                elif surface['type'] == 'det':
                    ray = ray.propagatedToTime(isec.t)
                else:
                    raise ValueError("Unknown optic type: {}".format(surface['type']))
                data['outray'] = ray
                out.append(data)
            return out
        else:
            rays = r
            for name, surface in self.surfaces.items():
                isecs = surface['surface'].intersect(rays)
                data = {'name':name, 'inrays':rays}
                if surface['type'] == 'mirror':
                    rays = jtrace._jtrace.reflectMany(isecs, rays)
                elif surface['type'] in ['lens', 'filter']:
                    rays = jtrace._jtrace.refractMany(isecs, rays, surface['m0'], surface['m1'])
                elif surface['type'] == 'det':
                    rays = jtrace._jtrace.propagatedToTimesMany(rays, isecs.t)
                else:
                    raise ValueError("Unknown optic type: {}".format(surface['type']))
                data['outrays'] = rays
                out.append(data)
            return out

    def huygensPSF(self, xs, ys, zs=None, rays=None, wavelength=None, theta_x=0, theta_y=0, nradii=5, naz=50):
        surfaceList = list(self.surfaces.values())
        if rays is None:
            # Generate some rays based on the first optic.
            s0 = surfaceList[0]
            rays = jtrace.parallelRays(
                z=10, outer=s0['outer'], inner=s0['inner'],
                theta_x=theta_x, theta_y=theta_y,
                nradii=nradii, naz=naz,
                wavelength=wavelength, medium=s0['m0']
            )
        rays = self.trace(rays)
        rays = jtrace.RayVector([r for r in rays if not r.isVignetted])
        if zs is None:
            zs = np.empty(xs.shape, dtype=np.float64)
            zs.fill(surfaceList[-1]['surface'].B)
        points = np.concatenate([aux[..., None] for aux in (xs, ys, zs)], axis=-1)
        time = rays[0].t0  # Doesn't actually matter, but use something close to intercept time
        amplitudes = np.empty(xs.shape, dtype=np.complex128)
        for (i, j) in np.ndindex(xs.shape):
            amplitudes[i, j] = np.sum(jtrace._jtrace.amplitudeMany(
                rays,
                jtrace.Vec3(*points[i, j]),
                time
            )
        )
        return np.abs(amplitudes)**2

    def exit_pupil_z(self, wavelength, theta=10./206265):
        # Trace a parabasal ray, i.e., a ray that goes through the center of the entrance pupil at a
        # small angle, and see where it intersects the optic axis again.  We're assuming here both
        # that the entrance pupil is coincident with the first surface (which is reasonable for most
        # telescopes), and that the optics are centered.
        point = jtrace.Vec3(0, 0, 0)
        th = 10./206265
        v = jtrace.Vec3(0.0, np.sin(theta), -np.cos(theta))
        m0 = list(self.surfaces.values())[0]['m0']
        v /= m0.getN(wavelength)
        r = jtrace.Ray(point, v, t=0, w=wavelength)
        # rewind a bit so we can find an intersection
        r = r.propagatedToTime(-1)
        r = self.trace(r)
        t = -r.y0/r.vy + r.t0
        XP = r.positionAtTime(t).z
        return XP

    def _reference_sphere(self, point, wavelength, theta=10./206265):
        XP = self.exit_pupil_z(wavelength, theta)
        ref_sphere_radius = XP - point.z
        return (jtrace.Sphere(-ref_sphere_radius, point.z+ref_sphere_radius)
                .shift(point.x, point.y, 0.0))

    def wavefront(self, theta_x, theta_y, wavelength, rays=None, nx=32):
        if rays is None:
            EP_size = list(self.surfaces.values())[0]['outer']
            m0 = list(self.surfaces.values())[0]['m0']
            rays = jtrace.rayGrid(
                    10, 2*EP_size,
                    theta_x=theta_x, theta_y=theta_y,
                    wavelength=wavelength, medium=m0, nx=nx)
        outrays = self.trace(rays)
        w = np.logical_not(outrays.isVignetted)
        point = jtrace.Vec3(np.mean(outrays.x[w]), np.mean(outrays.y[w]), np.mean(outrays.z[w]))
        ref_sphere = self._reference_sphere(point, wavelength)
        isecs = ref_sphere.intersect(outrays)
        wf = (isecs.t-np.mean(isecs.t[w]))/wavelength
        wf = np.ma.masked_array(wf, mask=outrays.isVignetted)
        return wf

    def fftPSF(self, theta_x, theta_y, wavelength, nx=32):
        wf = self.wavefront(theta_x, theta_y, wavelength, nx=nx).reshape(nx, nx)
        expwf = np.zeros((2*nx, 2*nx), dtype=np.complex128)
        expwf[nx//2:-nx//2, nx//2:-nx//2][~wf.mask] = np.exp(2j*np.pi*wf[~wf.mask])
        psf = np.abs(np.fft.fftshift(np.fft.fft2(np.fft.fftshift(expwf))))**2
        return psf

    def clone(self):
        cls = self.__class__
        out = cls.__new__(cls)
        out.__dict__.update(self.__dict__)
        out.surfaces = self.surfaces.copy()
        return out

    def withShift(self, surfaceId, dx, dy, dz):
        out = self.clone()
        if isinstance(surfaceId, numbers.Integral):
            container = out.surfaces.values()
        else:
            container = out.surfaces
        sdict = container[surfaceId].copy()
        surf = sdict['surface']
        sdict['surface'] = surf.shift(dx, dy, dz)
        container[surfaceId] = sdict
        return out

    def withRotX(self, surfaceId, theta):
        out = self.clone()
        if isinstance(surfaceId, numbers.Integral):
            container = out.surfaces.values()
        else:
            container = out.surfaces
        sorig = container[surfaceId]
        snew = sorig.rotX(theta)
        container[surfaceId] = snew
        return out

    def withRotY(self, surfaceId, theta):
        out = self.clone()
        if isinstance(surfaceId, numbers.Integral):
            container = out.surfaces.values()
        else:
            container = out.surfaces
        sorig = container[surfaceId]
        snew = sorig.rotY(theta)
        container[surfaceId] = snew
        return out

    def withRotZ(self, surfaceId, theta):
        out = self.clone()
        if isinstance(surfaceId, numbers.Integral):
            container = out.surfaces.values()
        else:
            container = out.surfaces
        sorig = container[surfaceId]
        snew = sorig.rotZ(theta)
        container[surfaceId] = snew
        return out
