#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
QSI workflow
=====
"""

import os
import os.path as op
from pathlib import Path
import logging
import sys
import gc
import uuid
import warnings
from argparse import ArgumentParser
from argparse import RawTextHelpFormatter
from multiprocessing import cpu_count
from time import strftime
warnings.filterwarnings("ignore", category=ImportWarning)

logging.addLevelName(25,
                     'IMPORTANT')  # Add a new level between INFO and WARNING
logging.addLevelName(15, 'VERBOSE')  # Add a new level between INFO and DEBUG
logger = logging.getLogger('cli')


def _warn_redirect(message, category, filename, lineno, file=None, line=None):
    logger.warning('Captured warning (%s): %s', category, message)


def check_deps(workflow):
    from nipype.utils.filemanip import which
    return sorted((node.interface.__class__.__name__, node.interface._cmd)
                  for node in workflow._get_all_nodes()
                  if (hasattr(node.interface, '_cmd')
                      and which(node.interface._cmd.split()[0]) is None))


def get_parser():
    """Build parser object"""
    from ..__about__ import __version__

    verstr = 'qsiprep v{}'.format(__version__)

    parser = ArgumentParser(
        description='qsiprep: fMRI PREProcessing workflows',
        formatter_class=RawTextHelpFormatter)

    # Arguments as specified by BIDS-Apps
    # required, positional arguments
    # IMPORTANT: they must go directly with the parser object
    parser.add_argument('--bids_dir', '--bids-dir',
                        type=os.path.abspath,
                        required=True,
                        action='store',
                        default='',
                        help='the root folder of a BIDS valid dataset (sub-XXXXX folders '
                        'should be found at the top level in this folder).')
    parser.add_argument('--output_dir', '--output-dir',
                        required=True,
                        action='store',
                        type=os.path.abspath,
                        default='',
                        help='the output path for the outcomes of preprocessing and visual'
                        ' reports')
    parser.add_argument('--analysis_level', '--analysis-level',
                        choices=['participant'],
                        required=True,
                        action='store',
                        help='processing stage to be run, only "participant" in the case of '
                        'qsiprep (see BIDS-Apps specification).')

    # optional arguments
    parser.add_argument('--version', action='version', version=verstr)

    g_bids = parser.add_argument_group('Options for filtering BIDS queries')
    g_bids.add_argument(
        '--participant_label',
        '--participant-label',
        action='store',
        nargs='+',
        help='a space delimited list of participant identifiers or a single '
        'identifier (the sub- prefix can be removed)')

    g_bids.add_argument(
        '-t',
        '--task-id',
        action='store',
        help='select a specific task to be processed')

    g_perfm = parser.add_argument_group('Options to handle performance')
    g_perfm.add_argument(
        '--nthreads',
        '--n_cpus',
        '-n-cpus',
        action='store',
        type=int,
        help='maximum number of threads across all processes')
    g_perfm.add_argument(
        '--omp-nthreads',
        action='store',
        type=int,
        default=0,
        help='maximum number of threads per-process')
    g_perfm.add_argument(
        '--mem_mb',
        '--mem-mb',
        action='store',
        default=0,
        type=int,
        help='upper bound memory limit for qsiprep processes')
    g_perfm.add_argument(
        '--low-mem',
        action='store_true',
        help='attempt to reduce memory usage (will increase disk usage '
        'in working directory)')
    g_perfm.add_argument(
        '--use-plugin',
        action='store',
        default=None,
        help='nipype plugin configuration file')
    g_perfm.add_argument(
        '--anat-only',
        action='store_true',
        help='run anatomical workflows only')
    g_perfm.add_argument(
        '--boilerplate', action='store_true', help='generate boilerplate only')
    g_perfm.add_argument(
        "-v",
        "--verbose",
        dest="verbose_count",
        action="count",
        default=0,
        help="increases log verbosity for each occurence, debug level is -vvv")

    g_conf = parser.add_argument_group('Workflow configuration')
    g_conf.add_argument(
        '--ignore',
        required=False,
        action='store',
        nargs="+",
        default=[],
        choices=['fieldmaps', 'sbref'],
        help='ignore selected aspects of the input dataset to disable '
        'corresponding parts of the workflow (a space delimited list)')
    g_conf.add_argument(
        '--longitudinal',
        action='store_true',
        help='treat dataset as longitudinal - may increase runtime')
    g_conf.add_argument(
        '--dwi_denoise_window', '--dwi-denoise-window',
        action='store',
        type=int,
        default=7,
        help='window size in voxels for ``dwidenoise``. Must be odd. '
             'If 0, ``dwidwenoise`` will not be run')
    g_conf.add_argument(
        '--denoise-before-combining', '--denoise_before_combining',
        action='store_true',
        help='run ``dwidenoise`` before combining dwis. Requires '
             '``--combine-all-dwis``')
    g_conf.add_argument(
        '--combine_all_dwis', '--combine-all-dwis',
        action='store_true',
        help='combine dwis from across multiple runs for motion correction '
        'and reconstruction.')
    g_conf.add_argument(
        '--discard-repeated-samples',
        action='store_true',
        help='discard repeats of q-space samples. Useful if using a '
        'regularized reconstruction method')
    g_conf.add_argument(
        '--write-local-bvecs',
        action='store_true',
        default=False,
        help='write a series of voxelwise bvecs')
    g_conf.add_argument(
        '--b0-to-t1w-transform',
        action='store',
        default="Rigid",
        choices=["Rigid", "Affine"],
        help='Degrees of freedom when registering b0 to T1w images. '
        '6 degrees (rotation and translation) are used by default.')
    g_conf.add_argument(
        '--output-space', '--output_space',
        required=True,
        action='store',
        choices=['T1w', 'template'],
        nargs='+',
        default=['T1w'],
        help='volume and surface spaces to resample functional series into\n'
        ' - T1w: subject anatomical volume\n'
        ' - template: normalization target specified by --template\n'
        'this argument can be single value or a space delimited list,\n'
        'for example: --output-space T1w template')
    g_conf.add_argument(
        '--template',
        required=False,
        action='store',
        choices=['MNI152NLin2009cAsym'],
        default='MNI152NLin2009cAsym',
        help='volume template space (default: MNI152NLin2009cAsym)')
    g_conf.add_argument(
        '--output-resolution', '--output_resolution',
        required=True,
        action='store',
        type=float,
        help='the isotropic voxel size in mm the data will be resampled to '
        'after preprocessing. If set to a lower value than the original voxel '
        'size, your data will be upsampled.'
        )

    g_moco = parser.add_argument_group(
        'Specific options for motion correction and coregistration')
    g_moco.add_argument(
        '--b0-motion-corr-to',
        action='store',
        default='iterative',
        choices=['iterative', 'first'],
        help='align to the "first" b0 volume or do an "iterative" registration'
        ' of all b0 image to their midpoint image (default: iterative)')
    g_moco.add_argument(
        '--hmc-transform',
        action='store',
        default='Affine',
        choices=['Affine', 'Rigid'],
        help='transformation to be optimized during head motion correction')
    g_moco.add_argument(
        '--hmc_model', '--hmc-model',
        action='store',
        default='3dSHORE',
        choices=['none', '3dSHORE', 'MAPMRI'],
        help='model used to generate target images for hmc. If "none" the '
        'non-b0 images will be warped using the same transform as their '
        'nearest b0 image')
    g_moco.add_argument(
        '--impute-slice-threshold',
        action='store',
        default=0,
        type=float,
        help='impute data in slices that are this many SDs from expected. '
        'If 0, no slices will be imputed')

    # ANTs options
    g_ants = parser.add_argument_group(
        'Specific options for ANTs registrations')
    g_ants.add_argument(
        '--skull-strip-template',
        action='store',
        default='OASIS',
        choices=['OASIS', 'NKI'],
        help='select ANTs skull-stripping template (default: OASIS))')
    g_ants.add_argument(
        '--skull-strip-fixed-seed',
        action='store_true',
        help='do not use a random seed for skull-stripping - will ensure '
        'run-to-run replicability when used with --omp-nthreads 1')

    # FreeSurfer options
    g_fs = parser.add_argument_group('Specific options for FreeSurfer preprocessing')
    g_fs.add_argument(
        '--fs-license-file', metavar='PATH', type=os.path.abspath,
        help='Path to FreeSurfer license key file. Get it (for free) by registering '
        'at https://surfer.nmr.mgh.harvard.edu/registration.html')

    # Fieldmap options
    g_fmap = parser.add_argument_group(
        'Specific options for handling fieldmaps')
    g_fmap.add_argument(
        '--prefer_dedicated_fmaps',
        action='store_true',
        default='false',
        help='forces unwarping to use files from the fmap directory instead '
        'of using an RPEdir scan from the same session.')
    g_fmap.add_argument(
        '--fmap-bspline',
        action='store_true',
        default=False,
        help='fit a B-Spline field using least-squares (experimental)')
    g_fmap.add_argument(
        '--fmap-no-demean',
        action='store_false',
        default=True,
        help='do not remove median (within mask) from fieldmap')

    # SyN-unwarp options
    g_syn = parser.add_argument_group(
        'Specific options for SyN distortion correction')
    g_syn.add_argument(
        '--use-syn-sdc',
        action='store_true',
        default=False,
        help='EXPERIMENTAL: Use fieldmap-free distortion correction')
    g_syn.add_argument(
        '--force-syn',
        action='store_true',
        default=False,
        help='EXPERIMENTAL/TEMPORARY: Use SyN correction in addition to '
        'fieldmap correction, if available')

    g_other = parser.add_argument_group('Other options')
    g_other.add_argument(
        '-w',
        '--work-dir',
        action='store',
        help='path where intermediate results should be stored')
    g_other.add_argument(
        '--resource-monitor',
        action='store_true',
        default=False,
        help='enable Nipype\'s resource monitoring to keep track of memory '
        'and CPU usage')
    g_other.add_argument(
        '--reports-only',
        action='store_true',
        default=False,
        help='only generate reports, don\'t run workflows. This will only '
        'rerun report aggregation, not reportlet generation for specific '
        'nodes.')
    g_other.add_argument(
        '--run-uuid',
        action='store',
        default=None,
        help='Specify UUID of previous run, to include error logs in report. '
        'No effect without --reports-only.')
    g_other.add_argument(
        '--write-graph',
        action='store_true',
        default=False,
        help='Write workflow graph.')
    g_other.add_argument(
        '--stop-on-first-crash',
        action='store_true',
        default=False,
        help='Force stopping on first crash, even if a work directory'
        ' was specified.')
    g_other.add_argument(
        '--notrack',
        action='store_true',
        default=False,
        help='Opt-out of sending tracking information of this run to '
        'the qsiprep developers. This information helps to '
        'improve qsiprep and provides an indicator of real '
        'world usage crucial for obtaining funding.')
    g_other.add_argument(
        '--sloppy',
        action='store_true',
        default=False,
        help='Use low-quality tools for speed - TESTING ONLY')

    return parser


def main():
    """Entry point"""
    from nipype import logging as nlogging
    from multiprocessing import set_start_method, Process, Manager
    from ..viz.reports import generate_reports
    from ..utils.bids import write_derivative_description
    set_start_method('forkserver')

    warnings.showwarning = _warn_redirect
    opts = get_parser().parse_args()

    # FreeSurfer license
    default_license = str(Path(str(os.getenv('FREESURFER_HOME'))) / 'license.txt')
    # Precedence: --fs-license-file, $FS_LICENSE, default_license
    license_file = opts.fs_license_file or os.getenv('FS_LICENSE',
                                                     default_license)
    if not os.path.exists(license_file):
        raise RuntimeError(
            'ERROR: a valid license file is required for FreeSurfer to run. '
            'qsiprep looked for an existing license file at several paths, in'
            'this order: 1) command line argument ``--fs-license-file``; 2) '
            '``$FS_LICENSE`` environment variable; and 3) the '
            '``$FREESURFER_HOME/license.txt`` path. '
            'Get it (for free) by registering at https://'
            'surfer.nmr.mgh.harvard.edu/registration.html')
    os.environ['FS_LICENSE'] = license_file

    # Retrieve logging level
    log_level = int(max(25 - 5 * opts.verbose_count, logging.DEBUG))
    # Set logging
    logger.setLevel(log_level)
    nlogging.getLogger('nipype.workflow').setLevel(log_level)
    nlogging.getLogger('nipype.interface').setLevel(log_level)
    nlogging.getLogger('nipype.utils').setLevel(log_level)

    errno = 0

    # Call build_workflow(opts, retval)
    with Manager() as mgr:
        retval = mgr.dict()
        p = Process(target=build_workflow, args=(opts, retval))
        p.start()
        p.join()

        if p.exitcode != 0:
            sys.exit(p.exitcode)

        qsiprep_wf = retval['workflow']
        plugin_settings = retval['plugin_settings']
        bids_dir = retval['bids_dir']
        output_dir = retval['output_dir']
        work_dir = retval['work_dir']
        subject_list = retval['subject_list']
        run_uuid = retval['run_uuid']
        retcode = retval['return_code']

    if qsiprep_wf is None:
        sys.exit(1)

    if opts.write_graph:
        qsiprep_wf.write_graph(
            graph2use="colored", format='svg', simple_form=True)

    if opts.reports_only:
        sys.exit(int(retcode > 0))

    if opts.boilerplate:
        sys.exit(int(retcode > 0))

    """
    # Sentry tracking
    if not opts.notrack:
        try:
            from raven import Client
            dev_user = bool(int(os.getenv('qsiprep_DEV', 0)))
            msg = 'qsiprep running%s' % (int(dev_user) * ' [dev]')
            client = Client(
                'https://d5a16b0c38d84d1584dfc93b9fb1ade6:'
                '21f3c516491847af8e4ed249b122c4af@sentry.io/1137693',
                release=__version__)
            client.captureMessage(
                message=msg,
                level='debug' if dev_user else 'info',
                tags={
                    'run_id': run_uuid,
                    'npart': len(subject_list),
                    'type': 'ping',
                    'dev': dev_user
                })
        except Exception:
            pass
    """

    # Check workflow for missing commands
    missing = check_deps(qsiprep_wf)
    if missing:
        print("Cannot run qsiprep. Missing dependencies:")
        for iface, cmd in missing:
            print("\t{} (Interface: {})".format(cmd, iface))
        sys.exit(2)

    # Clean up master process before running workflow, which may create forks
    gc.collect()
    try:
        qsiprep_wf.run(**plugin_settings)
    except RuntimeError as e:
        if "Workflow did not execute cleanly" in str(e):
            errno = 1
        else:
            raise

    # Generate reports phase
    errno += generate_reports(subject_list, output_dir, work_dir, run_uuid)
    write_derivative_description(bids_dir, str(Path(output_dir) / 'qsiprep'))
    sys.exit(int(errno > 0))


def build_workflow(opts, retval):
    """
    Create the Nipype Workflow that supports the whole execution
    graph, given the inputs.

    All the checks and the construction of the workflow are done
    inside this function that has pickleable inputs and output
    dictionary (``retval``) to allow isolation using a
    ``multiprocessing.Process`` that allows qsiprep to enforce
    a hard-limited memory-scope.

    """
    from subprocess import check_call, CalledProcessError, TimeoutExpired
    from pkg_resources import resource_filename as pkgrf

    from nipype import logging, config as ncfg
    from ..__about__ import __version__
    from ..workflows.base import init_qsiprep_wf
    from ..utils.bids import collect_participants
    from ..viz.reports import generate_reports

    logger = logging.getLogger('nipype.workflow')

    INIT_MSG = """
    Running qsiprep version {version}:
      * BIDS dataset path: {bids_dir}.
      * Participant list: {subject_list}.
      * Run identifier: {uuid}.
    """.format

    output_spaces = opts.output_space or []

    # Check output_space
    if 'template' not in output_spaces and (opts.use_syn_sdc
                                            or opts.force_syn):
        msg = [
            'SyN SDC correction requires T1 to MNI registration, but '
            '"template" is not specified in "--output-space" arguments.',
            'Option --use-syn will be cowardly dismissed.'
        ]
        if opts.force_syn:
            output_spaces.append('template')
            msg[1] = (
                ' Since --force-syn has been requested, "template" has been '
                'added to the "--output-space" list.')
        logger.warning(' '.join(msg))

    # Set up some instrumental utilities
    run_uuid = '%s_%s' % (strftime('%Y%m%d-%H%M%S'), uuid.uuid4())

    # First check that bids_dir looks like a BIDS folder
    bids_dir = os.path.abspath(opts.bids_dir)
    subject_list = collect_participants(
        bids_dir, participant_label=opts.participant_label)

    # Load base plugin_settings from file if --use-plugin
    if opts.use_plugin is not None:
        from yaml import load as loadyml
        with open(opts.use_plugin) as f:
            plugin_settings = loadyml(f)
        plugin_settings.setdefault('plugin_args', {})
    else:
        # Defaults
        plugin_settings = {
            'plugin': 'MultiProc',
            'plugin_args': {
                'raise_insufficient': False,
                'maxtasksperchild': 1,
            }
        }

    # Resource management options
    # Note that we're making strong assumptions about valid plugin args
    # This may need to be revisited if people try to use batch plugins
    nthreads = plugin_settings['plugin_args'].get('n_procs')
    # Permit overriding plugin config with specific CLI options
    if nthreads is None or opts.nthreads is not None:
        nthreads = opts.nthreads
        if nthreads is None or nthreads < 1:
            nthreads = cpu_count()
        plugin_settings['plugin_args']['n_procs'] = nthreads

    if opts.mem_mb:
        plugin_settings['plugin_args']['memory_gb'] = opts.mem_mb / 1024

    omp_nthreads = opts.omp_nthreads
    if omp_nthreads == 0:
        omp_nthreads = min(nthreads - 1 if nthreads > 1 else cpu_count(), 8)

    if 1 < nthreads < omp_nthreads:
        logger.warning(
            'Per-process threads (--omp-nthreads=%d) exceed total '
            'threads (--nthreads/--n_cpus=%d)', omp_nthreads, nthreads)

    # Set up directories
    output_dir = op.abspath(opts.output_dir)
    log_dir = op.join(output_dir, 'qsiprep', 'logs')
    work_dir = op.abspath(opts.work_dir or 'work')  # Set work/ as default

    # Check and create output and working directories
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    # Nipype config (logs and execution)
    ncfg.update_config({
        'logging': {
            'log_directory': log_dir,
            'log_to_file': True
        },
        'execution': {
            'crashdump_dir':
            log_dir,
            'crashfile_format':
            'txt',
            'get_linked_libs':
            False,
            'stop_on_first_crash':
            opts.stop_on_first_crash or opts.work_dir is None,
        },
        'monitoring': {
            'enabled': opts.resource_monitor,
            'sample_frequency': '0.5',
            'summary_append': True,
        }
    })

    if opts.resource_monitor:
        ncfg.enable_resource_monitor()

    retval['return_code'] = 0
    retval['plugin_settings'] = plugin_settings
    retval['bids_dir'] = bids_dir
    retval['output_dir'] = output_dir
    retval['work_dir'] = work_dir
    retval['subject_list'] = subject_list
    retval['run_uuid'] = run_uuid
    retval['workflow'] = None

    # Called with reports only
    if opts.reports_only:
        logger.log(25, 'Running --reports-only on participants %s',
                   ', '.join(subject_list))
        if opts.run_uuid is not None:
            run_uuid = opts.run_uuid
        retval['return_code'] = generate_reports(subject_list, output_dir,
                                                 work_dir, run_uuid)
        return retval

    # Build main workflow
    logger.log(
        25,
        INIT_MSG(
            version=__version__,
            bids_dir=bids_dir,
            subject_list=subject_list,
            uuid=run_uuid))

    retval['workflow'] = init_qsiprep_wf(
        subject_list=subject_list,
        run_uuid=run_uuid,
        work_dir=work_dir,
        output_dir=output_dir,
        ignore=opts.ignore,
        hires=False,
        freesurfer=False,
        debug=opts.sloppy,
        low_mem=opts.low_mem,
        anat_only=opts.anat_only,
        longitudinal=opts.longitudinal,
        combine_all_dwis=opts.combine_all_dwis,
        discard_repeated_samples=opts.discard_repeated_samples,
        dwi_denoise_window=opts.dwi_denoise_window,
        denoise_before_combining=opts.denoise_before_combining,
        write_local_bvecs=opts.write_local_bvecs,
        omp_nthreads=omp_nthreads,
        skull_strip_template=opts.skull_strip_template,
        skull_strip_fixed_seed=opts.skull_strip_fixed_seed,
        output_spaces=output_spaces,
        output_resolution=opts.output_resolution,
        template=opts.template,
        bids_dir=bids_dir,
        motion_corr_to=opts.b0_motion_corr_to,
        hmc_transform=opts.hmc_transform,
        hmc_model=opts.hmc_model,
        impute_slice_threshold=opts.impute_slice_threshold,
        b0_to_t1w_transform=opts.b0_to_t1w_transform,
        prefer_dedicated_fmaps=opts.prefer_dedicated_fmaps,
        fmap_bspline=opts.fmap_bspline,
        fmap_demean=opts.fmap_no_demean,
        use_syn=opts.use_syn_sdc,
        force_syn=opts.force_syn,
    )
    retval['return_code'] = 0

    logs_path = Path(output_dir) / 'qsiprep' / 'logs'
    boilerplate = retval['workflow'].visit_desc()
    (logs_path / 'CITATION.md').write_text(boilerplate)
    logger.log(
        25, 'Works derived from this qsiprep execution should '
        'include the following boilerplate:\n\n%s', boilerplate)

    # Generate HTML file resolving citations
    cmd = [
        'pandoc', '-s', '--bibliography',
        pkgrf('qsiprep', 'data/boilerplate.bib'), '--filter',
        'pandoc-citeproc',
        str(logs_path / 'CITATION.md'), '-o',
        str(logs_path / 'CITATION.html')
    ]
    try:
        check_call(cmd, timeout=10)
    except (FileNotFoundError, CalledProcessError, TimeoutExpired):
        logger.warning('Could not generate CITATION.html file:\n%s',
                       ' '.join(cmd))

    # Generate LaTex file resolving citations
    cmd = [
        'pandoc', '-s', '--bibliography',
        pkgrf('qsiprep', 'data/boilerplate.bib'), '--natbib',
        str(logs_path / 'CITATION.md'), '-o',
        str(logs_path / 'CITATION.tex')
    ]
    try:
        check_call(cmd, timeout=10)
    except (FileNotFoundError, CalledProcessError, TimeoutExpired):
        logger.warning('Could not generate CITATION.tex file:\n%s',
                       ' '.join(cmd))
    return retval


if __name__ == '__main__':
    raise RuntimeError(
        "qsiprep/cli/run.py should not be run directly;\n"
        "Please `pip install` qsiprep and use the `qsiprep` command")
