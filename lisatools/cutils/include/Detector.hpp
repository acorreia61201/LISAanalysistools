#ifndef __DETECTOR_HPP__
#define __DETECTOR_HPP__

#include "global.hpp"
#include <iostream>

class Vec
{
public:
    double x;
    double y;
    double z;

    CUDA_DEVICE
    Vec(double x_, double y_, double z_)
    {
        x = x_;
        y = y_;
        z = z_;
    }
};

class Orbits
{
public:
    double dt;
    int N;
    double *n_arr;
    double *ltt_arr;
    double *x_arr;
    int nlinks;
    int nspacecraft;
    int *links;
    int *sc_r;
    int *sc_e;

    Orbits(double dt_, int N_, double *n_arr_, double *ltt_arr_, double *x_arr_, int *links_, int *sc_r_, int *sc_e_)
    {
        dt = dt_;
        N = N_;
        n_arr = n_arr_;
        ltt_arr = ltt_arr_;
        x_arr = x_arr_;
        nlinks = 6;
        nspacecraft = 3;

        sc_r = sc_r_;
        sc_e = sc_e_;
        links = links_;

        // std::cout << " START " << std::endl;
        // for (int i = 0; i < nlinks; i += 1)
        // {
        //     sc_r[i] = sc_r_[i];
        //     sc_e[i] = sc_e_[i];
        //     links[i] = links_[i];
        //     // std::cout << i << " HAHAHAH " << sc_r_[i] << " " << sc_e_[i] << std::endl;
        // }
    };

    CUDA_DEVICE int get_window(double t);
    CUDA_DEVICE Vec get_normal_unit_vec(double t, int link);
    CUDA_DEVICE double interpolate(double t, double *in_arr, int window, int major_ndim, int major_ind, int ndim, int pos);
    CUDA_DEVICE int get_link_ind(int link);
    CUDA_DEVICE int get_sc_ind(int sc);
    CUDA_DEVICE double get_light_travel_time(double t, int link);
    CUDA_DEVICE Vec get_pos(double t, int sc);
    CUDA_DEVICE void get_normal_unit_vec_ptr(Vec *vec, double t, int link);
    CUDA_DEVICE void get_pos_ptr(Vec *vec, double t, int sc);
    void get_light_travel_time_arr(double *ltt, double *t, int *link, int num);
    void dealloc(){
        // delete[] links;
        // delete[] sc_r;
        // delete[] sc_e;
    };
};

#endif // __DETECTOR_HPP__