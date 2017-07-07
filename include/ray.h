#ifndef jtrace_ray_h
#define jtrace_ray_h

#include <sstream>
#include <string>
#include <complex>
#include <vector>
#include "vec3.h"

namespace jtrace {
    const double PI = 3.14159265358979323846;
    struct Ray {
        Ray(double x0, double y0, double z0, double vx, double vy, double vz,
            double t, double w, bool isVignetted);
        Ray(Vec3 _p0, Vec3 _v, double t, double w, bool isVignetted);
        Ray(std::array<double,3> _p0, std::array<double,3> _v,
            double t, double w, bool isVignetted);
        Ray(bool failed);
        Ray() = default;

        Vec3 p0; // reference position
        Vec3 v;  // "velocity" Vec3, really v/c
        double t0; // reference time, really c*t0
        double wavelength; // in vacuum, in meters
        bool isVignetted;
        bool failed;

        Vec3 positionAtTime(double t) const;
        Ray propagatedToTime(double t) const;
        bool operator==(const Ray&) const;
        bool operator!=(const Ray&) const;
        double getX0() const { return p0.x; }
        double getY0() const { return p0.y; }
        double getZ0() const { return p0.z; }
        double getVx() const { return v.x; }
        double getVy() const { return v.y; }
        double getVz() const { return v.z; }

        void setFail() { failed=true; }
        void clearFail() { failed=false; }

        std::string repr() const;

        Vec3 k() const { return 2 * PI * v / wavelength / v.Magnitude() / v.Magnitude(); }
        double omega() const { return 2 * PI / wavelength; }
        double phase(const Vec3& r, double t) const;
        std::complex<double> amplitude(const Vec3& r, double t) const;
    };

    inline std::ostream& operator<<(std::ostream& os, const Ray& r) {
        return os << r.repr();
    }

    std::vector<double> phaseMany(const std::vector<Ray>&, const Vec3& r, double t);
    std::vector<std::complex<double>> amplitudeMany(const std::vector<Ray>&, const Vec3& r, double t);
    std::vector<Ray> propagatedToTimesMany(const std::vector<Ray>&, const std::vector<double>& t);

}

#endif
