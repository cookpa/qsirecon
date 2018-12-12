from fmriprep.engine import Workflow
import nipype.pipeline.engine as pe
from nipype.interfaces import ants, afni, utility as niu
import pandas as pd
from dipy.core.geometry import decompose_matrix
from fmriprep.engine import Workflow
import os
import numpy as np
from ...interfaces.gradients import MatchTransforms, GradientRotation
from ...interfaces.dipy import IdealSignal

def combine_motion(motions):
    collected_motion = []
    for motion_file in motions:
        if os.path.exists("output.txt"):
            os.remove("output.txt")
        # Convert to homogenous matrix
        os.system("ConvertTransformFile 3 %s output.txt --RAS --hm" % (motion_file[0]))
        affine = np.loadtxt("output.txt")
        scale, shear, angles, translate, persp = decompose_matrix(affine)
        collected_motion.append(np.concatenate([scale, shear,
                                np.array(angles)*180/np.pi, translate]))

    final_motion = np.row_stack(collected_motion)
    cols = ["scaleX", "scaleY", "scaleZ", "shearXY", "shearXZ",
            "shearYZ", "rotateX", "rotateY", "rotateZ", "shiftX", "shiftY",
            "shiftZ"]
    motion_df = pd.DataFrame(data=final_motion, columns=cols)
    motion_df.to_csv("motion_params.csv", index=False)
    return os.path.abspath("motion_params.csv")


def linear_alignment_workflow(transform="Rigid",
                              metric="Mattes",
                              iternum=0,
                              spatial_bias_correct=False,
                              precision="fine"):
    """
    Takes a template image and a set of input images, does
    a linear alignment to the template and updates it with the
    inverse of the average affine transform to the new template

    Returns a workflow

    """
    iteration_wf = pe.Workflow(name="iterative_alignment_%03d" % iternum)
    input_node_fields = ["image_paths", "template_image", "iteration_num"]
    inputnode = pe.Node(
        niu.IdentityInterface(fields=input_node_fields), name='inputnode')
    inputnode.inputs.iteration_num = iternum
    outputnode = pe.Node(
        niu.IdentityInterface(fields=["registered_image_paths", "affine_transforms",
                                       "updated_template"]), name='outputnode')

    reg = ants.Registration()
    reg.inputs.metric = [metric]
    reg.inputs.transforms = [transform]
    reg.inputs.sigma_units = ["vox"]
    reg.inputs.sampling_strategy = ['Random']
    reg.inputs.sampling_percentage = [0.25]
    reg.inputs.radius_or_number_of_bins = [32]
    reg.inputs.initial_moving_transform_com = 0
    reg.inputs.interpolation = 'HammingWindowedSinc'
    reg.inputs.dimension = 3
    reg.inputs.winsorize_lower_quantile = 0.025
    reg.inputs.winsorize_upper_quantile = 0.975
    reg.inputs.convergence_threshold = [1e-06]
    reg.inputs.collapse_output_transforms = True
    reg.inputs.write_composite_transform = False
    reg.inputs.output_warped_image = True
    if precision == "coarse":
        reg.inputs.shrink_factors = [[4, 2]]
        reg.inputs.smoothing_sigmas = [[3., 1.]]
        reg.inputs.number_of_iterations = [[1000, 10000]]
        reg.inputs.transform_parameters = [[0.3]]
    else:
        reg.inputs.shrink_factors = [[4, 2, 1]]
        reg.inputs.smoothing_sigmas = [[3., 1., 0.]]
        reg.inputs.number_of_iterations = [[1000, 10000, 10000]]
        reg.inputs.transform_parameters = [[0.1]]
    iter_reg = pe.MapNode(
        reg, name="reg_%03d" % iternum, iterfield=["moving_image"])

    # Run the images through antsRegistration
    iteration_wf.connect(inputnode, "image_paths", iter_reg, "moving_image")
    iteration_wf.connect(inputnode, "template_image", iter_reg, "fixed_image")

    # Average the images
    averaged_images = pe.Node(
        ants.AverageImages(normalize=True, dimension=3),
        name="averaged_images")
    iteration_wf.connect(iter_reg, "warped_image", averaged_images, "images")

    # Apply the inverse to the average image
    transforms_to_list = pe.Node(niu.Merge(1), name="transforms_to_list")
    transforms_to_list.inputs.ravel_inputs = True
    iteration_wf.connect(iter_reg, "forward_transforms", transforms_to_list,
                         "in1")
    avg_affines = pe.Node(ants.AverageAffineTransform(), name="avg_affine")
    avg_affines.inputs.dimension = 3
    avg_affines.inputs.output_affine_transform = "AveragedAffines.mat"
    iteration_wf.connect(transforms_to_list, "out", avg_affines, "transforms")

    invert_average = pe.Node(ants.ApplyTransforms(), name="invert_average")
    invert_average.inputs.interpolation = "HammingWindowedSinc"
    invert_average.inputs.invert_transform_flags = [True]

    avg_to_list = pe.Node(niu.Merge(1), name="to_list")
    iteration_wf.connect(avg_affines, "affine_transform", avg_to_list, "in1")
    iteration_wf.connect(avg_to_list, "out", invert_average, "transforms")
    iteration_wf.connect(averaged_images, "output_average_image",
                         invert_average, "input_image")
    iteration_wf.connect(averaged_images, "output_average_image",
                         invert_average, "reference_image")
    iteration_wf.connect(invert_average, "output_image", outputnode,
                         "updated_template")
    iteration_wf.connect(iter_reg, "forward_transforms", outputnode,
                         "affine_transforms")
    iteration_wf.connect(iter_reg, "warped_image", outputnode,
                         "registered_image_paths")

    return iteration_wf


def init_b0_hmc_wf(align_to="iterative", transform="Rigid", spatial_bias_correct=False,
                   metric="Mattes", num_iters=3, name="b0_hmc_wf"):

    if align_to == "iterative" and num_iters < 2:
        raise ValueError("Must specify a positive number of iterations")

    alignment_wf = pe.Workflow(name=name)
    inputnode = pe.Node(
        niu.IdentityInterface(fields=['b0_images']), name='inputnode')
    outputnode = pe.Node(
        niu.IdentityInterface(fields=[
            "final_template", "forward_transforms", "iteration_templates",
            "motion_params"
        ]),
        name='outputnode')

    # Iteratively create a template
    if align_to == "iterative":
        initial_template = pe.Node(
            ants.AverageImages(normalize=True, dimension=3),
            name="initial_template")
        alignment_wf.connect(inputnode, "b0_images", initial_template,
                             "images")
        # Store the registration targets
        iter_templates = pe.Node(
            niu.Merge(num_iters), name="iteration_templates")
        alignment_wf.connect(initial_template, "output_average_image",
                             iter_templates, "in1")

        initial_reg = linear_alignment_workflow(
            transform=transform,
            metric=metric,
            spatial_bias_correct=spatial_bias_correct,
            precision="coarse",
            iternum=0)
        alignment_wf.connect(initial_template, "output_average_image",
                             initial_reg, "inputnode.template_image")
        alignment_wf.connect(inputnode, "b0_images", initial_reg,
                             "inputnode.image_paths")
        reg_iters = [initial_reg]
        for iternum in range(1, num_iters):
            reg_iters.append(
                linear_alignment_workflow(
                    transform=transform,
                    metric=metric,
                    spatial_bias_correct=spatial_bias_correct,
                    precision="fine",
                    iternum=iternum))
            alignment_wf.connect(reg_iters[-2], "outputnode.updated_template",
                                 reg_iters[-1], "inputnode.template_image")
            alignment_wf.connect(inputnode, "b0_images", reg_iters[-1],
                                 "inputnode.image_paths")
            alignment_wf.connect(reg_iters[-1], "outputnode.updated_template",
                                 iter_templates, "in%d" % (iternum + 1))

        # Compute distance travelled to the template
        summarize_motion = pe.Node(
            interface=niu.Function(
                input_names=["motions"],
                output_names=["stacked_motion"],
                function=combine_motion),
            name="summarize_motion")
        alignment_wf.connect(reg_iters[-1], "outputnode.affine_transforms",
                             summarize_motion, "motions")

        # Attach to outputs
        # The last iteration aligned to the output from the second-to-last
        alignment_wf.connect(reg_iters[-2], "outputnode.updated_template",
                             outputnode, "final_template")
        alignment_wf.connect(reg_iters[-1], "outputnode.affine_transforms",
                             outputnode, "forward_transforms")
        alignment_wf.connect(iter_templates, "out", outputnode,
                             "iteration_templates")
        alignment_wf.connect(summarize_motion, "stacked_motion", outputnode,
                             "motion_params")
    return alignment_wf


def model_alignment_workflow(modelname,
                             transform="Rigid",
                             metric="Mattes",
                             iternum=0,
                             spatial_bias_correct=False,
                             precision="fine",
                             replace_outliers=False):
    """Make a model-based hmc iteration."""
    workflow = pe.Workflow(name="model_alignment_%03d" % iternum)
    input_fields = ["dwi_files", "mask_image", "bvals", "bvecs", "transforms", "b0_image",
                    "b0_indices"]
    inputnode = pe.Node(
        niu.IdentityInterface(fields=input_fields), name='inputnode')
    inputnode.inputs.iteration_num = iternum
    outputnode = pe.Node(
        niu.IdentityInterface(fields=["warped_images", "transforms", "model"]), name='outputnode')

    generate_targets = pe.Node(IdealSignal(), name='generate_targets')

    reg = ants.Registration()
    reg.inputs.metric = [metric]
    reg.inputs.transforms = [transform]
    reg.inputs.sigma_units = ["vox"]
    reg.inputs.sampling_strategy = ['Random']
    reg.inputs.sampling_percentage = [0.25]
    reg.inputs.radius_or_number_of_bins = [32]
    reg.inputs.initial_moving_transform_com = 0
    reg.inputs.interpolation = 'LanczosWindowedSinc'
    reg.inputs.dimension = 3
    reg.inputs.winsorize_lower_quantile = 0.025
    reg.inputs.winsorize_upper_quantile = 0.975
    reg.inputs.convergence_threshold = [1e-06]
    reg.inputs.collapse_output_transforms = True
    reg.inputs.write_composite_transform = False
    reg.inputs.output_warped_image = True
    if precision == "coarse":
        reg.inputs.shrink_factors = [[4, 2]]
        reg.inputs.smoothing_sigmas = [[3., 1.]]
        reg.inputs.number_of_iterations = [[1000, 10000]]
        reg.inputs.transform_parameters = [[0.3]]
    else:
        reg.inputs.shrink_factors = [[4, 2, 1]]
        reg.inputs.smoothing_sigmas = [[3., 1., 0.]]
        reg.inputs.number_of_iterations = [[1000, 10000, 10000]]
        reg.inputs.transform_parameters = [[0.1]]
    iter_reg = pe.MapNode(
        reg, name="reg_%03d" % iternum, iterfield=["fixed_image", "moving_image"])

    # Run the images through antsRegistration
    workflow.connect(inputnode, "image_paths", iter_reg, "moving_image")
    return workflow


def init_dwi_model_hmc_wf(modelname, transform, mem_gb, omp_nthreads,
                          name='dwi_model_hmc_wf', metric="Mattes",
                          num_iters=2):
    """Create a model-based hmc workflow.

    .. workflow::
        :graph2use: colored
        :simple_form: yes

        from qsiprep.workflows.dwi import init_dwi_model_hmc_wf
        wf = init_dwi_model_hmc_wf(modelname='3dSHORE',
                                   transform='Affine',
                                   num_iters=2,
                                   mem_gb=3,
                                   omp_nthreads=1)

    **Parameters**

        modelname : str
            one of the models for reconstructing an EAP and producing
            signal estimates used for motion correction
        transform : str
            either "Rigid" or "Affine". Choosing "Affine" may help with Eddy warping
        num_iters : int
            the number of times the model will be updated with transformed data

    **Inputs**

        dwi_files
            list of 3d dwi files
        b0_indices
            list of which indices in `dwi_files` are b0 images
        b0_transforms
            list of transforms from b0s to the b0 template
        b0_template
            template to which b0s are registered
        b0_mask
            mask of containing brain voxels
        bvecs
            list of bvec files corresponding to `dwi_files`
        bvals
            list of bval files corresponding to `dwi_files`

    **Outputs**

        hmc_transforms
            list of transforms, one per file in `dwi_files`
        hmc_confounds
            file containing motion and qc parameters from hmc

    """
    workflow = Workflow(name=name)
    inputnode = pe.Node(niu.IdentityInterface(
        fields=['dwi_files', 'b0_indices', 'b0_transforms', 'b0_template', 'b0_mask', 'bvecs',
                'bvals']), name='inputnode')
    outputnode = pe.Node(niu.IdentityInterface(
        fields=['hmc_transforms', 'hmc_confounds', 'outlier_replaced_dwis']), name='inputnode')

    # Initialize with transform from nearest b0
    match_transforms = pe.Node(MatchTransforms(), name="match_transforms")
    initial_transforms = pe.MapNode(ants.ApplyTransforms(), iterfield=['dwi_files', 'transforms'],
                                    name="initial_transforms")
    workflow.connect([
        (inputnode, match_transforms, [('dwi_files', 'dwi_files'),
                                       ('b0_indices', 'b0_indices'),
                                       ('transforms', 'transforms')]),
        (inputnode, initial_transforms, [('dwi_files', 'input_image'),
                                         ('template_image', 'reference_image')]),
        (match_transforms, initial_transforms, [('transforms', 'transforms')])
    ])

    # Store the models
    iter_models = pe.Node(
        niu.Merge(num_iters), name="iteration_templates")

    # model-reg for the first iteration
    initial_reg = model_alignment_workflow(modelname, transform=transform, metric=metric,
                                           iternum=0, precision="coarse")
    workflow.connect([
        (initial_transforms, initial_reg, [('output_image', 'inputnode.dwi_files')]),
        (inputnode, initial_reg, [('bvals', 'inputnode.bvals'),
                                  ('bvecs', 'inputnode.bvecs'),
                                  ('b0_indices', 'inputnode.b0_indices'),
                                  ('b0_mask', 'inputnode.mask_image')]),

    ])

    reg_iters = [initial_reg]
    for iternum in range(1, num_iters):
        replace_outliers = (iternum == num_iters-1)
        reg_iters.append(
            model_alignment_workflow(modelname,
                                     transform=transform,
                                     metric=metric,
                                     precision="fine",
                                     iternum=iternum,
                                     replace_outliers=replace_outliers))

        workflow.connect([
            (inputnode, reg_iters[-1], [('bvals', 'inputnode.bvals'),
                                        ('bvecs', 'inputnode.bvecs'),
                                        ('b0_indices', 'inputnode.b0_indices'),
                                        ('b0_mask', 'inputnode.mask_image')]),
            (reg_iters[-2], reg_iters[-1], [('outputnode.warped_images', 'inputnode.dwi_files')]),
            (reg_iters[-1], iter_models, [('outputnode.model', "in%d" % (iternum + 1))])
        ])
    final_reg_iter = reg_iters[-1]

    # Grab motion, errors, etc
    calculate_confounds = pe.Node(name="calculate_confounds")

    workflow.connect([
        (iter_models, calculate_confounds, [('outputnode.models', 'models')]),
        (calculate_confounds, outputnode, [('confounds', 'confounds')]),
        (final_reg_iter, outputnode, [('outputnode.transforms', 'hmc_transforms'),
                                      ('outputnode.outlier_free_dwis', 'outlier_replaced_dwis')]),

    ])
