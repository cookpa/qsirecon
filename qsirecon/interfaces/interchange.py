from nipype.interfaces.base import (
    BaseInterfaceInputSpec,
    SimpleInterface,
    TraitedSpec,
    traits,
)

from qsirecon.interfaces.anatomical import QSIPrepAnatomicalIngress
from qsirecon.interfaces.ingress import QSIPrepDWIIngress

# Anatomical (t1w/t2w) slots
FS_FILES_TO_REGISTER = [
    # The skull-stripped brain produced by FreeSurfer
    "brain",
    # Automated volumetric segmentation file from FreeSurfer
    "aseg",
]
CREATEABLE_ANATOMICAL_OUTPUTS = [
    "fs_5tt_hsvs",
    "qsiprep_5tt_hsvs",
    "qsiprep_5tt_fast",
    "fs_to_qsiprep_transform_itk",
    "fs_to_qsiprep_transform_mrtrix",
]

# These come directly from QSIPrep outputs. They're aligned to the DWIs in AC-PC
qsiprep_highres_anatomical_ingressed_fields = (
    QSIPrepAnatomicalIngress.output_spec.class_editable_traits()
)

# The init_recon_anatomical anatomical workflow can create additional
# anatomical files (segmentations/masks/etc) that can be used downstream.
# These are **independent** of the DWI data and handled separately
anatomical_workflow_outputs = (
    qsiprep_highres_anatomical_ingressed_fields
    + FS_FILES_TO_REGISTER
    + CREATEABLE_ANATOMICAL_OUTPUTS
)

# These are read directly from QSIRecon's dwi results.
qsiprep_output_names = QSIPrepDWIIngress().output_spec.class_editable_traits()

# dMRI + registered anatomical fields
recon_workflow_anatomical_input_fields = anatomical_workflow_outputs + [
    "dwi_mask",
    "atlas_configs",
    "odf_rois",
    "resampling_template",
    "mapping_metadata",
]

# Check that no conflicts have been introduced
overlapping_names = set(qsiprep_output_names).intersection(recon_workflow_anatomical_input_fields)
if overlapping_names:
    raise Exception(
        "Someone has added overlapping outputs between the anatomical "
        "and dwi inputs: " + " ".join(overlapping_names)
    )

recon_workflow_input_fields = qsiprep_output_names + recon_workflow_anatomical_input_fields
default_input_set = set(recon_workflow_input_fields)
default_connections = [(trait, trait) for trait in recon_workflow_input_fields]


class _ReconWorkflowInputsInputSpec(BaseInterfaceInputSpec):
    pass


class _ReconWorkflowInputsOutputSpec(TraitedSpec):
    pass


class ReconWorkflowInputs(SimpleInterface):
    input_spec = _ReconWorkflowInputsInputSpec
    output_spec = _ReconWorkflowInputsOutputSpec

    def _run_interface(self, runtime):
        inputs = self.inputs.get()
        for name in recon_workflow_input_fields:
            self._results[name] = inputs.get(name)
        return runtime


for name in recon_workflow_input_fields:
    _ReconWorkflowInputsInputSpec.add_class_trait(name, traits.Any)
    _ReconWorkflowInputsOutputSpec.add_class_trait(name, traits.Any)


class _ReconAnatomicalDataInputSpec(BaseInterfaceInputSpec):
    pass


class _ReconAnatomicalDataOutputSpec(TraitedSpec):
    pass


class ReconAnatomicalData(SimpleInterface):
    input_spec = _ReconAnatomicalDataInputSpec
    output_spec = _ReconAnatomicalDataOutputSpec

    def _run_interface(self, runtime):
        inputs = self.inputs.get()
        for name in anatomical_workflow_outputs:
            self._results[name] = inputs.get(name)
        return runtime


for name in anatomical_workflow_outputs:
    _ReconAnatomicalDataInputSpec.add_class_trait(name, traits.Any)
    _ReconAnatomicalDataOutputSpec.add_class_trait(name, traits.Any)
