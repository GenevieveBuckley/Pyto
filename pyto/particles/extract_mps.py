"""
Extract particles from pyto.spatial.MultiParticleSets particle sets.

# Author: Vladan Lucic 
# $Id$
"""

__version__ = "$Revision$"

import os
import pickle
from copy import copy, deepcopy
import re
import itertools

import numpy as np
from numpy.random import default_rng
import scipy as sp
from scipy.spatial.distance import cdist
import pandas as pd
import skimage
 
import pyto
from ..geometry.rigid_3d import Rigid3D
from .set_path import SetPath
from pyto.particles.relion_tools import get_array_data, write_table
from pyto.projects.presynaptic import Presynaptic, tomo_generator
from .extract_mps_filter import ExtractMPSFilter
from ..spatial.multi_particle_sets import MultiParticleSets


class ExtractMPS(ExtractMPSFilter):
    """Extract particles from pyto.spatial.MultiParticleSets particle sets.

    Filtering methods are implemented in ExtractMPSFilter
    """

    def __init__(
            self, 
            distance_col='distance', normal_source_index_col='source_index', 
            normal_source_suffix='_source',
            normal_angle_cols=['normal_theta', 'normal_phi'],
            degree=True, centers_dtype=int,
            tomo_col='tomo',
            tomo_id_mode='munc13', path_label='rlnMicrographName',
            path_label_morse='psSegImage',
            id_source_label=None, ctf_label='rlnCtfImage', check_ctf=False,
            tomo_ids=None, box_size=None,
            label_format=None,            
            region_bin=1, region_bin_factor=1, remove_region_initial=True,
            init_coord_cols=None,
            center_init_frame_cols=None, center_reg_frame_cols=None, 
            tomo_l_corner_cols=None, tomo_r_corner_cols=None,
            reg_l_corner_cols=None, reg_r_corner_cols=None,
            tomo_inside_col='tomo_inside', reg_inside_col='region_inside',   
            tomo_particle_col='tomo_particle',
            region_particle_col='reg_particle',
            in_tomo_particle_col='in_tomo_particle',
            root_template='particles_size-{size}',
            class_names=[], class_code={},
            rng=None, seed=None):
        """Sets attributes from arguments.
        """

        self.distance_col = distance_col
        self.normal_source_index_col = normal_source_index_col
        self.normal_source_suffix = normal_source_suffix
        self.normal_angle_cols = normal_angle_cols

        #mps_defaults = pyto.spatial.MultiParticleSets()
        #if coord_cols is None:
        #    self.coord_cols = mps_default.
        #if center_coord-cols is None:
        #    self.center_coord_cols = mps_default
        self.degree=degree
        self.centers_dtype = centers_dtype

        self.tomo_col = tomo_col

        self.tomo_id_mode = tomo_id_mode
        self.path_label = path_label
        self.path_label_morse = path_label_morse
        self.id_source_label = id_source_label
        self.init_coord_cols = init_coord_cols
        self.ctf_label = ctf_label
        self.check_ctf = check_ctf

        self.tomo_ids = tomo_ids

        self.box_size = box_size

        self.tomo_l_corner_cols = tomo_l_corner_cols
        self.tomo_r_corner_cols = tomo_r_corner_cols
        self.reg_l_corner_cols = reg_l_corner_cols
        self.reg_r_corner_cols = reg_r_corner_cols
        self.tomo_inside_col = tomo_inside_col
        self.reg_inside_col = reg_inside_col
        
        self.remove_region_initial = remove_region_initial
        self.region_bin = region_bin
        self.region_bin_factor = region_bin_factor
        self.center_init_frame_cols = center_init_frame_cols
        self.center_reg_frame_cols = center_reg_frame_cols
 
        
        if label_format is None:
            self.label_format ={
                'rlnMicrographName': '%s', 'rlnCtfImage': '%s', 
                'rlnImageName': '%s', 'rlnCoordinateX': '%d', 
                'rlnCoordinateY': '%d', 
                'rlnCoordinateZ': '%d', 
                'rlnAngleTilt': '%8.3f', 'rlnAngleTiltPrior': '%8.3f', 
                'rlnAnglePsi': '%8.3f', 'rlnAnglePsiPrior': '%8.3f', 
                'rlnAngleRot': '%8.3f'}
        else:
            self.label_format = label_format

        self.tomo_particle_col = tomo_particle_col
        self.region_particle_col = region_particle_col
        self.in_tomo_particle_col = in_tomo_particle_col
        
        self.root_template = root_template

        self.class_names = class_names
        self.class_code = class_code

        self.rng = rng
        self.seed = seed

    def extract_particles_task(
            self,
            particles_path, source_path, reverse, name, particle_to_center,
            regions_star_path, ctf_star_path, 
            clean_initial=True, use_priors=True, randomize_rot=True,
            expand_particle=False, expand_region=True,
            mean=0, std=1, invert_contrast=True,
            name_prefix='particle_', name_suffix='',
            convert_path_common=None, convert_path_helper=None,
            
            write_particles=True, write_regions=False, morse_regions=False,
            verbose=True, star_comment='Particles'
            ):
        """Extracts particles from tomos.

        In the current workflow (2.2024), arguments write_regions and 
        morse_regions should be both False because region images are
        generated by self.extract_regions_task().

        Uses original particle coordinates, that is before projection on 
        thin region.

        Calculates particle centers (using normals) first in the thin 
        regions frame and then converts to the initial (full tomo size) frame
 

        """

        # Read MPS particles and source where particles are already
        #  converted to the region image frames and clean them
        mps_init = MultiParticleSets.read(particles_path, verbose=verbose)
        if clean_initial:
            mps_init.particles = mps_init.particles[
                mps_init.particles[mps_init.keep_col]]
        source = MultiParticleSets.read(source_path, verbose=verbose)

        # set normals
        mps_part = self.set_normals(
            mps=mps_init, source=source,
            mps_coord_cols=mps_init.orig_coord_reg_frame_cols, 
            source_coord_cols=source.orig_coord_reg_frame_cols, reverse=reverse, 
            use_priors=use_priors)

        # setup particle mps
        mps = deepcopy(source)
        mps.tomos = source.tomos
        mps.particles = mps_part
        mps.name = name
        # assumes thin region and region images have the same positioning
        if randomize_rot:
            mps.particles['rlnAngleRot'] = 360 * np.random.rand(
                mps.particles.shape[0])

        # determine centers in reg frame
        mps.center_reg_frame_cols = self.center_reg_frame_cols
        self.project_along_normals(
            mps=mps, coord_cols=mps.orig_coord_reg_frame_cols, 
            center_coord_cols=mps.center_reg_frame_cols,
            distance=particle_to_center, update=True)

        # convert centers back to init frame
        mps.center_init_frame_cols = self.center_init_frame_cols
        self.convert_back(
            mps=mps, init_cols=mps.center_reg_frame_cols, 
            final_cols=mps.center_init_frame_cols, update=True)

        # find tomo and segmentation image paths
        mps.tomo_col = self.tomo_col
        self.add_paths(
            mps=mps, star_path=regions_star_path, mode='tomos',
            path_col=mps.tomo_col, update=True)
        if morse_regions:
            self.add_paths(
                mps=mps, star_path=regions_star_path, mode='tomos',
                path_label=self.path_label_morse,
                path_col=mps.tomo_col, update=True)

        # add ctf paths
        self.add_ctf(
            mps=mps, star_path=ctf_star_path, update=True,
            check=self.check_ctf)

        # select tomos and adjust paths
        self.convert_paths(
            mps=mps, 
            common=convert_path_common, helper_path=convert_path_helper, 
            path_cols=[self.ctf_label], tomo_path_col=mps.tomo_col, 
            region_path_col=None, update=True)
        
        # find corners and label particles that fit inside tomo and
        # segmentation images
        mps.tomo_l_corner_cols = self.tomo_l_corner_cols
        mps.tomo_r_corner_cols = self.tomo_r_corner_cols
        mps.tomo_inside_col = self.tomo_inside_col
        mps.reg_inside_col = self.reg_inside_col
        mps.reg_l_corner_cols = self.reg_l_corner_cols
        mps.reg_r_corner_cols = self.reg_r_corner_cols
        self.find_corners(
            mps=mps, image_path_col=mps.tomo_col, box_size=self.box_size, 
            coord_cols=self.center_init_frame_cols,
            l_corner_cols=self.tomo_l_corner_cols, 
            r_corner_cols=self.tomo_r_corner_cols,
            column=self.tomo_inside_col, update=True)
        if morse_regions:
            self.find_corners(
                mps=mps, image_path_col=mps.region_col, box_size=self.box_size, 
                coord_cols=self.center_reg_frame_cols,
                l_corner_cols=self.reg_l_corner_cols, 
                r_corner_cols=self.reg_r_corner_cols,
                column=mps.reg_inside_col, update=True)

        # setup paths
        paths = Paths(
            name=name, root_template=self.root_template, size=self.box_size)

        # extract particles
        mps.tomo_particle_col = self.tomo_particle_col
        self.write_particles(
            mps=mps, l_corner_cols=self.tomo_l_corner_cols,
            r_corner_cols=self.tomo_r_corner_cols, 
            image_path_col=mps.tomo_col, dir_=paths.particles_dir,
            expand=expand_particle, select_col=mps.tomo_inside_col,
            mean=mean, std=std, invert_contrast=invert_contrast,
            name_prefix=name_prefix, name_suffix=name_suffix,
            particle_path_col=mps.tomo_particle_col,
            convert_path_common=convert_path_common, 
            convert_path_helper=convert_path_helper,
            update=True, write=write_particles)

        # extract segments
        if morse_regions:
            mps.region_particle_col = self.region_particle_col
            self.write_particles(
                mps=mps, l_corner_cols=self.reg_l_corner_cols,
                r_corner_cols=self.reg_r_corner_cols, 
                image_path_col=mps.region_col, dir_=paths.regions_dir,
                expand=expand_region, select_col=mps.tomo_inside_col,
                mean=None, std=None,
                name_prefix='seg_', name_suffix='',
                particle_path_col=mps.region_particle_col,
                convert_path_common=convert_path_common, 
                convert_path_helper=convert_path_helper,
                update=True, write=write_regions)

        # save particle mps
        mps.write(path=paths.mps_path_tmp, verbose=True)

        # write star files and the corresponding table
        labels = self.get_labels(mps=mps)
        combined = self.make_star(
            mps=mps, labels=labels, star_path=paths.star_path,
            verbose=True, comment=star_comment)

        # make star file for each particle subclass
        self.split_star(
            mps=mps,
            class_names=self.class_names, class_code=self.class_code,
            labels=self.get_labels(mps), 
            star_path=paths.star_path, star_comment=star_comment)
        
    def extract_regions_task(
            self, mps, scalar, indexed, struct_path_col,
            region_path_mode, 
            convert_path_common=None, convert_path_helper=None,
            path_col=None, offset_cols=None, shape_cols=None, bin_col=None,
            expand=True,
            normalize_kwargs={}, dilate=None, out_dtype=None,
            fun=None, fun_kwargs={},
            write_regions=True, regions_name='regions',
            name_prefix='seg_', name_suffix='', mps_path=None,
            star_comment='Regions'):
        """Extracts regions from 

        Saves the modified MultiParticleSets at the (standard) location
        specified by self.root_template and arg regions_name. In addition, 
        if arg mps_path is specified, the same file is also saved at the
        specified location. The later is meant to write the pickle in the
        original particles tables dir.

        If arg fun is specified, args normalize_bound_fun are mag_fun
        ignored. To apply a normalization, magnification and another
        function, set args:
          - fun=(normalize_fun, mag_fun, other_fun)
          - fun_kwargs=(normalize_fun_kwargs, mag_fun_kwargs, other_fun_kwargs)
        Each function has to accept (ndarray) image as the first argument
        and return (ndarry) a modified image. The functions are applied 
        in the order they are specified in arg fun.

        In 'pkl_segment' mode (arg region_path_mode), all other segments
        that may be present in a particle image are removed before
        applying functions specified by arg fun.

        Arguments:

          - write_regions: flag indicating if region images are written
          - regions_name: name of the regions, used as the directory name 
          where region images are saved, if None 'regions' is used
          - name_prefix: part of the region image name before the 
          particle id (default 'seg_')
          - name_suffix: part of the region image name after the 
          particle id and before extension (default '')

          - mps_path: if specified, the resulting MultiParticleSets pickle
          is saved at this path (in addition to saving it at the  
          standard path

        """

        # setup paths
        paths = Paths(
            name=regions_name, root_template=self.root_template,
            regions=regions_name, size=self.box_size)
            
        # remove region related columns    
        if self.remove_region_initial:
            self.remove_region_cols(mps=mps) 

        # set attributes to mps
        mps.tomo_l_corner_cols = self.tomo_l_corner_cols
        mps.tomo_r_corner_cols = self.tomo_r_corner_cols
        mps.reg_l_corner_cols = self.reg_l_corner_cols
        mps.reg_r_corner_cols = self.reg_r_corner_cols
        mps.tomo_inside_col = self.tomo_inside_col
        mps.reg_inside_col = self.reg_inside_col
        mps.tomo_particle_col = self.tomo_particle_col
        mps.region_particle_col = self.region_particle_col 

        # convert coords to regions
        mps = self.convert_to_struct_region(  
            mps=mps, scalar=scalar, indexed=indexed,
            struct_path_col=struct_path_col, image_path_mode=region_path_mode,
            init_coord_cols=self.init_coord_cols,
            region_coord_cols=self.center_reg_frame_cols, 
            convert_path_common=convert_path_common,
            convert_path_helper=convert_path_helper,
            region_bin=self.region_bin, path_col=path_col, 
            offset_cols=offset_cols, shape_cols=shape_cols, bin_col=bin_col)

        # find corner coords in regions frame
        particle_size_loc = self.box_size // self.region_bin_factor
        self.find_corners(
            mps=mps, image_path_col=mps.region_col, box_size=particle_size_loc,
            coord_cols=self.center_reg_frame_cols,
            l_corner_cols=self.reg_l_corner_cols,
            r_corner_cols=self.reg_r_corner_cols, 
            shape_cols=mps.region_shape_cols, column=self.reg_inside_col,
            update=True)

        # prepare image processing functions (magnify, normalize, dilate)
        if fun is None:
            fun, fun_kwargs = self.prepare_func(
                zoom_factor=self.region_bin_factor,
                normalize_kwargs=normalize_kwargs, dilate=dilate,
                dtype=out_dtype)            
            #fun = (normalize_bound_fun, mag_fun)
            #fun_kwargs = (normalize_bound_fun_kwargs, mag_fun_kwargs)
        else:
            if ((len(normalize_kwargs) > 0)
                or (dilate is not None) or (out_dtype is not None)):
                print("Warning: Because argument fun is specified, arguments "
                      + f"normalize_kwargs ({normalize_kwargs}), "
                      + f"dilate ({dilate}) "
                      + f"and dtype ({dtype}) are ignored. ")

        # write images
        self.write_particles(
            mps=mps, l_corner_cols=self.reg_l_corner_cols,
            r_corner_cols=self.reg_r_corner_cols, 
            image_path_col=mps.region_col, image_path_mode=region_path_mode,
            dir_=paths.regions_dir, 
            expand=expand, select_col=mps.tomo_inside_col,
            mean=None, std=None, fun=fun, fun_kwargs=fun_kwargs,
            name_prefix=name_prefix, name_suffix=name_suffix,
            particle_path_col=mps.region_particle_col,
            convert_path_common=convert_path_common, 
            convert_path_helper=convert_path_helper,
            write=write_regions, update=True)
        
        # save mps locally 
        mps.write(path=paths.mps_path, verbose=True)

        # save the same mps also in another place if mps_path is given 
        if mps_path is not None:
            mps.write(path=mps_path, verbose=True)

        #  make star file
        labels = self.get_labels(mps=mps)
        self.make_star(
            mps=mps, labels=labels, star_path=paths.star_path,
            comment=star_comment, verbose=True)
        
        # make star file for each particle subclass
        self.split_star(
            mps=mps,
            class_names=self.class_names, class_code=self.class_code,
            labels=self.get_labels(mps), 
            star_path=paths.star_path, star_comment=star_comment)
       
    def set_normals(
            self, mps, source, mps_coord_cols, source_coord_cols,
            reverse=False, use_priors=True):
        """Find membrane normals from another particle set.

        For each particle specified in the (arg) mps particle set, finds the 
        closest particle from (arg) source particle set and assignes the source
        angles to the corresponding mps particle.

        Values of the following columns are copied from the closest elements of
        source.particles: 
          - source.particle_rotation_labels, reversed if arg reverse=True 
          (see below)
          - source.particle_id_col, 
          - source.class_name_col, 
          - source.class_number_col: 
        The column names stay the same. In case source.particles colum names 
        overlap with those of the mps.particles, suffix source_suffix 
        is added 

        In addition the following columns to mps.particles are introduced:
          - distance_col: distance to the closest element of source.particles
          - source_index_col: index of the closest element of source.particles
          - normal_angle_cols: values of normal angles theta and phi

        If arg reverse is True, Euler angles (columns 
        mps.particle_rotation_labels) 
        are changed so that they define the opposite direction:
            - phi, theta, psi -> phi + pi, pi - theta, psi + pi
        In this case, normal vector angles are determined from the reversed 
        Eulers 

        Table source.particles have to contain at least one set of Euler 
        angles, that is ('rlnAngleRot', 'rlnAngleTilt', 'rlnAnglePsi'), or 
        ('rlnAngleRot', 'rlnAngleTiltPrior', 'rlnAnglePsiPrior')

        Returns mps.particles with the added columns
        """

        # find closest pre to all tethers
        min_dist = mps.find_min_distances(
            df_1=mps.particles, df_2=source.particles,
            group_col=mps.tomo_id_col,
            coord_cols_1=mps_coord_cols, coord_cols_2=source_coord_cols, 
            distance_col=self.distance_col,
            ind_col_2=self.normal_source_index_col)

        # add the closest pre to tethers table
        part_1 = mps.particles.join(min_dist[
            [self.normal_source_index_col, self.distance_col]])

        # add the corresponding angles from source to mps (particles)
        source_cols = (
            source.particle_rotation_labels 
            + [source.particle_id_col, source.class_name_col,
               source.class_number_col])
        part_2 = pd.merge(  # index from df_2 particles
            part_1, source.particles[source_cols], how='left', 
            left_on=self.normal_source_index_col, right_index=True, sort=False,
            suffixes=['', self.normal_source_suffix])

        # reverse Eulers if needed
        if reverse:
            angle_cols = [
                'rlnAngleRot', 'rlnAngleTiltPrior', 'rlnAnglePsiPrior']
            try:
                priors = part_2.apply(
                    lambda x: pd.Series(
                        Rigid3D.reverse_euler(
                            angles=x[angle_cols].to_numpy(), degree=True),
                        index=angle_cols),
                    axis=1, result_type='expand')
                prior_failed = False
            except KeyError:
                prior_failed = True
            else:
                part_2.update(priors)

            angle_cols = ['rlnAngleRot', 'rlnAngleTilt', 'rlnAnglePsi']
            try:
                posteriors = part_2.apply(
                    lambda x: pd.Series(
                        Rigid3D.reverse_euler(
                            angles=x[angle_cols].to_numpy(), degree=True),
                        index=angle_cols),
                    axis=1, result_type='expand')
            except KeyError:
                pass
            else:
                if prior_failed:
                    part_2.update(posteriors)
                else:
                    part_2.update(posteriors[['rlnAngleTilt', 'rlnAnglePsi']])

        # select angles to make normals
        if use_priors:
            tilt_name = 'rlnAngleTiltPrior'
            psi_name = 'rlnAnglePsiPrior'
        else:
            tilt_name = 'rlnAngleTilt'
            psi_name = 'rlnAnglePsi'

        # get normal angles and put in table
        normals = part_2.apply(
            lambda x: (
                self.find_spherical(
                    angles=[x[tilt_name], x[psi_name]], relion=True,
                    reverse=False)),
             axis=1)
        theta = normals.apply(lambda x: x[0]).rename(self.normal_angle_cols[0])
        phi = normals.apply(lambda x: x[1]).rename(self.normal_angle_cols[1])
        part_2 = part_2.join([theta, phi])

        return part_2

    @classmethod
    def find_spherical(
            cls, angles, relion=False, euler_mode='zxz_ex_active',
            degree=False, reverse=False):
        """Wrapper for LineProjection.find_spherical.
        """
        line_proj = pyto.spatial.LineProjection(
            relion=relion, euler_mode=euler_mode, degree=degree,
            reverse=reverse)
        return line_proj.find_spherical(angles=angles)

    @classmethod
    def project_along_line(cls, theta, phi, distance=1, degree=False):
        """Wrapper for LineProjection.project_along_line().
        """
        line_proj = pyto.spatial.LineProjection(degree=degree)
        res = line_proj.project_along_line(
            theta=theta, phi=phi, distance=distance)
        return res

    def project_along_normals(
            self, mps, coord_cols, center_coord_cols, distance, 
            update=False):
        """Project particles of multiple tomos along membrane normals. 

        Meant to determine particle image centers coordinates as a 
        fixed displacement from particle coords along membrane normals, 
        when membrane normals are given in the relion format 
        ('rlnAngleTiltPrior' and 'rlnAnglePsiPrior').

        """

        if (distance is not None) and (distance != 0): 

            # find centers
            centers = mps.particles.apply(
                lambda x: (
                    self.project_along_line(
                        theta=x[self.normal_angle_cols[0]],
                        phi=x[self.normal_angle_cols[1]], 
                        distance=distance, degree=self.degree)
                    + x[coord_cols]), 
                axis=1)
            if self.centers_dtype is not None:
                centers = centers.round().astype(self.centers_dtype)

        else:
            centers = mps.particles[coord_cols]

        # rename center columns
        columns_rename = dict(
            [(old, new) for old, new in zip(coord_cols, center_coord_cols)])
        centers.rename(columns=columns_rename, inplace=True)

        # update or return
        if update:
            mps.particles = mps.particles.join(centers)
        else:
            return centers
        
    def convert_back(self, mps, init_cols, final_cols, update=False):
        """Converts coordinates from region to initial frame.

        """

        column_rename = dict(
            [(old, new) for old, new in zip(init_cols, final_cols)])
        part_by_tomos = mps.particles.groupby(mps.tomo_id_col)
        converted_list = []

        # convert for each tomo separately
        for t_id, ind in part_by_tomos.groups.items():
            tomo_row = mps.tomos[mps.tomos[mps.tomo_id_col] == t_id]
            offsets = tomo_row[mps.region_offset_cols].to_numpy()[0]
            conv = mps.particles.loc[ind, init_cols] + offsets
            converted_list.append(conv)

        # add converted to original data
        converted = pd.concat(converted_list, axis=0)
        converted.rename(columns=column_rename, inplace=True)
        result = mps.particles.join(converted, how='left')

        if update:
            mps.particles = result
        else:
            return result

    def add_paths(
            self, mps, star_path, path_col, mode,
            path_label=None, update=False):
        """Adds tomo or region image paths to tomos or particles table

        Arguments:
          - path_label: if None, self.path_label is used
        """

        if path_label is None:
            path_label = self.path_label
        if self.id_source_label is None:
            id_source_label = mps.micrograph_label
        else:
            id_source_label = self.id_source_label
            
        # read star that contains segmentation path
        star = pd.DataFrame(get_array_data(
            starfile=star_path, tablename='data', types=str))
        star[mps.tomo_id_col] = star[id_source_label].map(
            lambda x: pyto.spatial.coloc_functions.get_tomo_id(
                path=x, mode=self.tomo_id_mode))
        star = star[[mps.tomo_id_col, path_label]].rename(
            columns={path_label: path_col}).copy()

        # add to table
        if mode == 'tomos':
            result = (mps.tomos  # keep mps.tomos index
                .reset_index()
                .merge(star, on=mps.tomo_id_col, how='left', sort=False)
                .set_index('index'))
        elif mode == 'particles':
            result = (mps.particles  # keep mps.particles index
                .reset_index()
                .merge(star, on=mps.tomo_id_col, how='left', sort=False)
                .set_index('index'))
        else:
            raise ValueError(f"Arg mode ({mode}) can be 'tomos' or 'particles'.")
        result[path_col] = result[path_col].astype('string')

        if update:
            if mode == 'tomos':
                mps.tomos = result
            elif mode == 'particles':
                mps.particles = result
        else:
            return result

    def add_ctf(
            self, mps, star_path, update=False, check=False):
        """Adds ctf path to tomos table.

        Ctf path is read from column self.ctf_label of star
        file (arg) star_path. The path is added to mps.tomos table,
        column self.ctf_label.

        Arguments:
          - mps:
          - star_path: path to the star file containing ctf path
          - update: flag indication if mps.tomos is updated to contain
          ctf path column
          - check: probably not needed

        Returns: modified tomos table if update is False, otherwise None 
        """

        # convert ctf star to dataframe
        template = pd.DataFrame(
            get_array_data(
                starfile=star_path, tablename='data', types=str))
        labels = template.columns.copy()

        # get tomo ids
        template[mps.tomo_id_col] = template[mps.micrograph_label].map(
            lambda x: pyto.spatial.coloc_functions.get_tomo_id(
                path=x, mode=self.tomo_id_mode))

        # keep only tomo and ctf paths
        paths_tab = template[
            [mps.tomo_id_col, mps.micrograph_label, self.ctf_label]].copy()
        paths_tab.drop_duplicates(inplace=True, ignore_index=True)
        paths_tab[self.ctf_label] = \
            paths_tab[self.ctf_label].astype('string')

        # add ctf info to tomos table
        result = (mps.tomos
            .reset_index()
            .merge(paths_tab, on=mps.tomo_id_col, how='left',
                   suffixes=('', '_test'))
            .set_index('index'))

        # just to check if tables and star have the same tomo paths (remove?)
        if check and not result[mps.tomo_col].eq(
                result[mps.micrograph_label]).all():
            raise ValueError(
                f"Tomo paths in columns {mps.tomo_col} and 'rlnMicrographName' "
                + f"are not the same")
        result.drop(columns=[mps.micrograph_label], inplace=True)

        if update:
            mps.tomos = result
        else:
            return result

    def convert_paths(
            self, mps, common, helper_path, path_cols=None,
            tomo_path_col=None, region_path_col=None, update=False):
        """Converts paths image and segmentation paths to another root

        Used for, but not limited to image and segmentation paths 

        If self.tomo_ids is not None, selects the specified tomos.

        """

        # select tomos
        if self.tomo_ids is not None:
            if update:
                mps.select(tomo_ids=self.tomo_ids, update=update)
            else:
                result = mps.select(tomo_ids=self.tomo_ids, update=update)
        if (self.tomo_ids is None) or update:
            result = mps

        # convert
        set_path = SetPath(common=common, helper_path=helper_path)
        if tomo_path_col is not None:
            result.tomos[tomo_path_col] = result.tomos[tomo_path_col].map(
                lambda x: set_path.convert_path(x))
            result.tomos[tomo_path_col] = \
                result.tomos[tomo_path_col].astype('string')
        if region_path_col is not None:
            result.tomos[region_path_col] = result.tomos[region_path_col].map(
                lambda x: set_path.convert_path(x))        
            result.tomos[region_path_col] = \
                result.tomos[region_path_col].astype('string')
        if path_cols is not None:
            for pa_col in path_cols:
                result.tomos[pa_col] = result.tomos[pa_col].map(
                    lambda x: set_path.convert_path(x))
                result.tomos[pa_col] = \
                    result.tomos[pa_col].astype('string')
                

        if not update:
            return result
        
    def find_corners(
            self, mps, image_path_col, box_size, coord_cols,
            l_corner_cols, r_corner_cols, 
            shape_cols=None, column='inside', update=False):
        """Find particle box corners and label those that fit inside.

        Applicable to (greyscale) tomograms or segmentations, depending 
        on parameters, 

        For real particles that are directly extracted from tomo, arg
        box_size is the intended particle size. However, for regions
        (segmentations) where regions are binned with respect to the
        corresponding particle tomo, arg box_size should be the size
        of the box extracted from regions. For example, if region tomos
        are binned 2x with respect to particle tomos, arg box size 
        should be half of the particle box size.

        Arguments:
          - box_size: particle size in the frame of the image from which 
          particles are extracted (in pixels)
          - shape_col: names of columns that contain image shape, if None 
          (default) shape is determined from the header of the image
        """

        # center - l corner and center - r corner distances
        center_coord = box_size // 2
        center_plus = box_size - center_coord    

        # convert particle image centers for each tomo separately
        res_list = []
        part_by_tomos = mps.particles.groupby(mps.tomo_id_col)
        for t_id, ind in part_by_tomos.groups.items():

            # get corner coords
            coords = mps.particles.loc[ind, coord_cols]
            l_corner = coords - center_coord
            l_corner.columns = l_corner_cols
            r_corner = coords + center_plus
            r_corner.columns = r_corner_cols

            # get tomo shape
            tomo_row = mps.tomos[mps.tomos[mps.tomo_id_col] == t_id]
            if shape_cols is not None:
                shape = tomo_row[shape_cols].to_numpy()[0]
            else:
                image_path = tomo_row[image_path_col].to_numpy()[0]
                image = pyto.io.ImageIO()
                image.readHeader(file=image_path)
                shape = np.asarray(image.shape)

            # find inside / outside
            inside = (
                (l_corner >= 0).all(axis=1) & (r_corner < shape).all(axis=1))
            inside.rename(column, inplace=True)

            # put corners and inside together
            res_list.append(l_corner.join([r_corner, inside]))

        # add converted to original data
        res_tab = pd.concat(res_list, axis=0)

        if update:
            result = mps.particles.join(res_tab, how='left')
            mps.particles = result
        else:
            return res_tab

    @classmethod
    def write_particles(
            cls, mps, l_corner_cols, r_corner_cols, image_path_col, dir_,
            expand, select_col=None, 
            mean=None, std=None, invert_contrast=False, fun=None, fun_kwargs={}, 
            image_path_mode='image', name_prefix='particle_', name_suffix='', 
            particle_path_col='particle', convert_path_common=None,
            convert_path_helper=None, update=False, write=True):
        """Writes particle or boundary subtomos.

        In 'pkl_segment' mode (arg image_path_mode), all other segments
        that may be present in a particle image are removed before
        applying functions specified by arg fun.
        """

        # remove outside particles
        parts_tab = mps.particles
        if select_col is not None:
            parts_tab = parts_tab[parts_tab[select_col]]

        # set flag to make particle images containing only one particle 
        keep_id_only = False
        if image_path_mode == 'pkl_segment':
            keep_id_only = True
            
        #
        p_indices = []
        path_list = []

        # loop over tomos
        part_by_tomos = parts_tab.groupby(mps.tomo_id_col)
        for tomo_id, ind in part_by_tomos.groups.items():

            # get tomo data
            tomo_row = mps.tomos[mps.tomos[mps.tomo_id_col] == tomo_id]        
            tomo_path = tomo_row[image_path_col].to_numpy()[0]

            if image_path_mode == 'image':
                image = pyto.core.Image.read(
                    file=tomo_path, header=True, memmap=True)
                pixelsize = image.pixelsize
                header = image.header

            elif ((image_path_mode == 'pkl_boundary')
                  or (image_path_mode == 'pkl_segment')):
                scene = pickle.load(open(tomo_path, 'rb'), encoding='latin1')
                if image_path_mode == 'pkl_boundary':
                    image = scene.boundary
                else:
                    image = scene.labels
                pixelsize = tomo_row[mps.pixel_nm_col].to_numpy()[0]
                header = None
                #image.write(file=f"bound_{tomo_id}.mrc")

            else:
                raise ValueError(
                    f"Argument image_path_mode {image_path_mode} was not "
                    + "undrstood.")

            # loop over particles
            for p_ind, row in parts_tab.loc[ind].iterrows():

                # make slice objects
                l_corner = row[l_corner_cols].to_numpy()
                r_corner = row[r_corner_cols].to_numpy()
                slices = [
                    slice(left, right) for left, right
                    in zip(l_corner, r_corner)]

                # get particle data
                particle_data = image.useInset(
                    inset=slices, mode=u'relative', expand=expand, update=False,
                    returnCopy=True)

                # process particle
                if std is not None:
                    particle_data = std * particle_data / particle_data.std()
                if mean is not None:
                    particle_data = particle_data - particle_data.mean() + mean
                if invert_contrast:
                    particle_data = -particle_data
                if keep_id_only:
                    particle_id = row[mps.particle_id_col]
                    particle_data[particle_data != particle_id] = 0
                if fun is not None:
                    if isinstance(fun, (tuple, list)):
                        for fun_one, fun_kwargs_one in zip(fun, fun_kwargs):
                            particle_data = fun_one(
                                particle_data, **fun_kwargs_one)    
                    else:
                        particle_data = fun(particle_data, **fun_kwargs)
                        
                # write particle
                particle_id = row[mps.particle_id_col]
                particle_path = os.path.abspath(os.path.join(
                    dir_, tomo_id,
                    f"{name_prefix}{particle_id}{name_suffix}.mrc"))
                particle = pyto.core.Image(data=particle_data)    
                try:
                    if write:
                        particle.write(
                            file=particle_path, header=header, pixel=pixelsize)
                    else:
                        pass
                        #print(f"As if writing {particle_path}")
                except IOError:
                    os.makedirs(os.path.dirname(particle_path))
                    if write:
                        particle.write(
                            file=particle_path, header=header, pixel=pixelsize)
                    else:
                        print(f"As if writing {particle_path}")

                # add particle path to row
                p_indices.append(p_ind)
                path_list.append(particle_path)

        # add all particle paths to table
        particle_path = pd.DataFrame(
            {particle_path_col: path_list}, index=p_indices)
        parts_tab = parts_tab.join(particle_path, how='left')
        set_path = SetPath(
            common=convert_path_common, helper_path=convert_path_helper)
        try:
            parts_tab[particle_path_col] = parts_tab[particle_path_col].map(
                lambda x: set_path.convert_path(x))
        except ValueError:
            pass
        parts_tab[particle_path_col] = \
            parts_tab[particle_path_col].astype('string')

        if update:
            mps.particles = parts_tab
        else:
            return parts_tab

    def make_star(
            self, mps, labels, star_path=None,
            comment="From MPS", verbose=False):
        """Writes star from particles and tomos.

        Arguments:
          - star_path: path to the out star file, or None for not writing
        """

        # find labels that are in tomos
        tomo_labels = dict([
            (lab, col) for lab, col in labels.items()
            if not col in mps.particles.columns])
        tomo_clean = mps.tomos[[mps.tomo_id_col] + list(tomo_labels.values())]

        # put tomo info to particles
        combined = (mps.particles
            .reset_index()
            .merge(tomo_clean, on=mps.tomo_id_col, how='left',
                   suffixes=('_bad', ''), sort=False)
            .set_index('index'))

        # convert data to dict
        data = {}
        for lab, col in labels.items():
            data[lab] = combined[col].to_numpy()

        # write 
        if star_path is not None:

            # write star file
            try:
                write_table(
                    starfile=star_path, labels=list(labels.keys()), data=data, 
                    format_=self.label_format, tablename='data', delimiter=' ', 
                    comment=f"# {comment}")
            except FileNotFoundError:
                os.makedirs(os.path.dirname(star_path))
                write_table(
                    starfile=star_path, labels=list(labels.keys()), data=data, 
                    format_=self.label_format, tablename='data', delimiter=' ', 
                    comment=f"# {comment}")                 

            # DataFrame corresponding to the star file
            star_sp = star_path.rsplit('.', 1)
            tab_path = f"{star_sp[0]}_{star_sp[1]}.pkl"
            pyto.io.PandasIO.write(
                table=combined, base=tab_path, file_formats=['json'],
                verbose=verbose,
                out_desc="DataFrame version of particle star file")

        return combined

    def split_star(
            self, mps, class_code, labels, star_path,
            class_names=None, star_comment=None):
        """Makes star files for each subclass separately

        """

        # write star file for each subclass separately
        for number, subclass in class_code.items():
            mps_curr = mps.select(
                class_names=class_names, class_numbers=[number], update=False)
            star_path_curr = star_path.replace('_all.star', f'_{subclass}.star')
            star_comment_curr = star_comment.replace(
                'All', subclass.capitalize())
            self.make_star(
                mps=mps_curr, labels=labels, 
                star_path=star_path_curr, verbose=True,
                comment=star_comment_curr)

    def get_labels(self, mps):
        """Make star file labels
        """
        labels_loc = {
            'rlnMicrographName': mps.tomo_col, 'rlnCtfImage': self.ctf_label, 
            'rlnImageName': self.tomo_particle_col,
            'rlnCoordinateX': mps.center_init_frame_cols[0], 
            'rlnCoordinateY': mps.center_init_frame_cols[1], 
            'rlnCoordinateZ': mps.center_init_frame_cols[2], 
            'rlnAngleTilt': 'rlnAngleTiltPrior',
            'rlnAngleTiltPrior': 'rlnAngleTiltPrior', 
            'rlnAnglePsi': 'rlnAnglePsiPrior',
            'rlnAnglePsiPrior': 'rlnAnglePsiPrior', 
            'rlnAngleRot': 'rlnAngleRot'}
        return labels_loc

    @classmethod
    def remove_region_cols(cls, mps):
        """Removes columns that contain region info

        """
        drop_cols = ([mps.region_col, mps.region_id_col] 
                     + mps.region_offset_cols + mps.region_shape_cols)
        for col in drop_cols:
            try:
                mps.tomos.drop(columns=[col], inplace=True)
            except KeyError:
                pass
        drop_cols = (
            mps.orig_coord_reg_frame_cols + mps.coord_reg_frame_cols 
            + mps.center_reg_frame_cols
            + mps.reg_l_corner_cols  + mps.reg_r_corner_cols
            + [mps.reg_inside_col])
        for col in drop_cols:
            try:
                mps.particles.drop(columns=[col], inplace=True)
            except KeyError:
                pass

    def convert_to_struct_region(
            self, mps, scalar, indexed, struct_path_col, image_path_mode,
            init_coord_cols, region_coord_cols, 
            convert_path_common=None, convert_path_helper=None,
            region_bin=1, path_col=None, offset_cols=None,
            shape_cols=None, bin_col=None):
        """Converts coordinates to region contained in a structure pickle.

        Arguments:
          - struct_segment: attribute of structure object that holds
          the image, can be 'boundary' for boundaries (regions) or 
          'labels' (same as 'hierarchy') for segmented particles
          - offset_cols, shape_cols: names of columns containing offsets 
          and shape of regions images
        """

        # default columns
        if path_col is None:
            path_col = mps.region_col
        if offset_cols is None:
            offset_cols = mps.region_offset_cols    
        if shape_cols is None:
            shape_cols = mps.region_shape_cols
        if bin_col is None:
            bin_col = mps.region_bin_col

        tomos = mps.tomos.copy()
        part_list = []
        for to_id, scalar_one, indexed_one, scene in tomo_generator(
            scalar=scalar, indexed=indexed, identifiers=self.tomo_ids, 
            pickle_var=struct_path_col, convert_path_common=convert_path_common, 
            convert_path_helper=convert_path_helper):

            # check if tomo exists in mps.tomos
            try:
                tomo_ind = \
                    mps.tomos[mps.tomos[mps.tomo_id_col] == to_id].index[0]
            except IndexError:
                continue

            # get boundary object and extract data
            scene_pkl_path = scalar_one[struct_path_col]
            if image_path_mode == 'pkl_boundary':
                bound = scene.boundary
            elif image_path_mode == 'pkl_segment':
                bound = scene.labels
            offsets = [sl.start for sl in bound.inset]
            shape = bound.data.shape

            # add boundary pickle path, offset and shape to tomos
            tomos.loc[tomo_ind, path_col] = scene_pkl_path
            tomos.loc[tomo_ind, offset_cols] = offsets
            tomos.loc[tomo_ind, shape_cols] = shape
            tomos.loc[tomo_ind, bin_col] = region_bin
            bin_fact = tomos.loc[tomo_ind, mps.coord_bin_col] / region_bin

            # extract particles for the current tomo
            part_one = \
                mps.particles[mps.particles[mps.tomo_id_col]==to_id].copy()

            # convert coords
            coords_orig = part_one[init_coord_cols].to_numpy()
            coords_final = (
                bin_fact * coords_orig - np.asarray(offsets).reshape(1, -1))
            #coords_final = np.rint(coords_final).astype(int)
            part_one[region_coord_cols] = coords_final
            part_list.append(part_one)

        # make full particles table
        converted_part = pd.concat(part_list, axis=0)
        converted_part[region_coord_cols] = \
            converted_part[region_coord_cols].round().astype(int)

        result = deepcopy(mps)
        result.tomos = tomos
        result.particles = converted_part

        return result

    def prepare_func(
            self, zoom_factor=1, zoom_order=0, normalize_kwargs={},
            dilate=None, dtype=None):
        """Prepare functions and arguments that modify images
    
        """

        fun = []
        fun_kwargs = []
        if zoom_factor != 1:
            fun.append(sp.ndimage.zoom)
            fun_kwargs.append({'zoom': zoom_factor, 'order': zoom_order})
        if len(normalize_kwargs) > 0:
            fun.append(self.normalize_bound_ids)
            fun_kwargs.append(normalize_kwargs)
        if (dilate is not None) and (dilate != 0):
            fun.append(sp.ndimage.grey_dilation)
            structure = skimage.morphology.ball(dilate)
            fun_kwargs.append({'footprint': structure})
        if dtype is not None:
            fun.append(np.asarray)
            fun_kwargs.append({'dtype': dtype})
            
        return fun, fun_kwargs
            
    @classmethod
    def normalize_bound_ids(
            cls, data, min_id_old, id_new, id_conversion={},
            dtype=np.int16):
        """Sets boundary ids to the specified (normalized) values.

        Used for segmeted images such as those showing boundaries, regions
        or other segments. Makes a new image where pixel values of the 
        initial image (arg data) are replaced as follows: 
          - all values that are >= (arg) min_id_old are replaced by 
          (arg) id_new
          - pixels having values equal to keys of (arg) id_conversion 
          are replaced by their corresponding values
          - all other pixels are set to 0

        The resulting boundary image contains boundaries only for
        the ids specified by id_new and values of id_conversion. 

        For example if initially vesicles have labels [10, 11, 12, ...],
        plasma membrane 2 and cytoplasmic region 3, and the intended
        value for all vesicles is 8, plasma membrane 4 and cytosol 1, use:
          normalize_bound_ids(
              data, min_id_old=5, id_new=10, id_conversion={2: 4, 3: 1})

        If the resulting image is meant to be saved, (arg) dtype has to be 
        one of the allowed data types for the intened image format.

        Arguments:
          - data: (ndarray) initial image
          - min_id_old: min value of all ids that are replaced by id_new 
          - id_new: replacement calue for >= min_id_old
          - id_conversion: (dict) 1-1 mapping old_value: new_value
          - dtype: dtype of the final image (default np.int16)

        Returns (ndarray) modified image
        """

        new_data = np.where(data>=min_id_old, id_new, 0)
        for old, new in id_conversion.items():
            new_data += np.where(data==old, new, 0)
        if dtype is not None:
            new_data = new_data.astype(dtype)

        return new_data

                
class Paths:
    """Contains attributes specifying particle and table paths
    """
    
    def __init__(
            self, name, root_template='particles_size-{size}',
            regions='regions', size=64):
        self.name = name
        self.root_template = root_template
        self.root = root_template.format(size=size)
        self.regions = regions
        self.size = size
        
    @property
    def particles_root(self):
        #return f'../particles_bin-2_size-{self.size}'
        return self.root
    
    @property
    def particles_dir(self):
        return os.path.join(self.particles_root, self.name)

    @property
    def regions_dir(self):
        return os.path.join(self.particles_root, self.regions)
    
    @property
    def mps_path(self):
        return os.path.join(self.particles_dir, f'tables/{self.name}.pkl')
    
    @property
    def mps_path_tmp(self):
        return os.path.join(self.particles_dir, f'tables/{self.name}_tmp.pkl')
    
    @property
    def star_path(self):
        return os.path.join(self.particles_dir, f'tables/{self.name}_all.star')

    
