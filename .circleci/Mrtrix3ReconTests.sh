#!/bin/bash

cat << DOC

Reconstruction workflow tests
=============================

All supported reconstruction workflows get tested

This tests the following features:
 - Blip-up + Blip-down DWI series for TOPUP/Eddy
 - Eddy is run on a CPU
 - Denoising is skipped
 - A follow-up reconstruction using the dsi_studio_gqi workflow

Inputs:
-------

 - qsirecon single shell results (data/DSDTI_fmap)
 - qsirecon multi shell results (data/DSDTI_fmap)

DOC
set +e
source ./get_data.sh
TESTDIR=${PWD}
get_config_data ${TESTDIR}
get_bids_data ${TESTDIR} singleshell_output
get_bids_data ${TESTDIR} multishell_output
CFG=${TESTDIR}/data/nipype.cfg
EDDY_CFG=${TESTDIR}/data/eddy_config.json
export FS_LICENSE=${TESTDIR}/data/license.txt

# Test MRtrix3 multishell msmt with ACT
TESTNAME=mrtrix_multishell_msmt_test
setup_dir ${TESTDIR}/${TESTNAME}
TEMPDIR=${TESTDIR}/${TESTNAME}/work
OUTPUT_DIR=${TESTDIR}/${TESTNAME}/derivatives
BIDS_INPUT_DIR=${TESTDIR}/data/multishell_output/qsirecon
QSIRECON_CMD=$(run_qsirecon_cmd ${BIDS_INPUT_DIR} ${OUTPUT_DIR})

${QSIRECON_CMD} \
	 -w ${TEMPDIR} \
	 --recon-input ${BIDS_INPUT_DIR} \
	 --sloppy \
     --stop-on-first-crash \
	 --recon-spec mrtrix_multishell_msmt_ACT-fast \
	 --recon-only \
	 -vv

# # Test MRtrix3 multishell msmt without ACT
# TESTNAME=mrtrix_multishell_msmt_noACT_test
# setup_dir ${TESTDIR}/${TESTNAME}
# TEMPDIR=${TESTDIR}/${TESTNAME}/work
# OUTPUT_DIR=${TESTDIR}/${TESTNAME}/derivatives
# BIDS_INPUT_DIR=${TESTDIR}/data/multishell_output/qsirecon
# QSIRECON_CMD=$(run_qsirecon_cmd ${BIDS_INPUT_DIR} ${OUTPUT_DIR})

# ${QSIRECON_CMD} \
# 	 -w ${TEMPDIR} \
# 	 --recon-input ${BIDS_INPUT_DIR} \
# 	 --sloppy \
# 	 --recon-spec mrtrix_multishell_msmt_noACT \
# 	 --recon-only \
# 	 -vv

