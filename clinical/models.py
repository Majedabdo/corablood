from django.db import models
from core.models import User
from donors.models import Donor

class DonorWorkflow(models.Model):
    """
    Represents a single donation visit/session.
    Acts as the 'Dependency Lock' preventing skips.
    """
    class Step(models.TextChoices):
        REGISTRATION = 'REGISTRATION', 'Registration'
        QUESTIONNAIRE = 'QUESTIONNAIRE', 'Medical Questionnaire'
        MEDICATION = 'MEDICATION', 'Medication Review'
        VITALS = 'VITALS', 'Vital Signs'
        COLLECTION = 'COLLECTION', 'Blood Collection'
        POST_DONATION = 'POST_DONATION', 'Post-Donation Care'
        ADVERSE_REACTION = 'ADVERSE_REACTION', 'Adverse Reaction'
        SURVEY = 'SURVEY', 'Post-Donation Survey'
        DEFERRED = 'DEFERRED', 'Deferred'
        PRE_SEPARATION = 'PRE_SEPARATION', 'Pre-Separation Checks'
        COMPONENTS = 'COMPONENTS', 'Component Extraction'
        ATTACHMENT = 'ATTACHMENT', 'Attachments'
        LABEL = 'LABEL', 'Labeling'
        LABS = 'LABS', 'Laboratory Testing'
        SELF_EXCLUSION = 'SELF_EXCLUSION', 'Self Exclusion'
        STATUS_HISTORY = 'STATUS_HISTORY', 'Status History'
        COMPLETED = 'COMPLETED', 'History'

    class WorkflowType(models.TextChoices):
        WHOLE_BLOOD = 'WHOLE_BLOOD', 'Whole Blood Donation'
        APHERESIS = 'APHERESIS', 'Apheresis Donation'

    donor = models.ForeignKey(Donor, on_delete=models.CASCADE, related_name='workflows')
    appointment = models.OneToOneField('donors.DonorAppointment', on_delete=models.SET_NULL, null=True, blank=True, related_name='workflow')
    status = models.CharField(max_length=20, choices=Step.choices, default=Step.REGISTRATION, db_index=True)
    workflow_type = models.CharField(
        max_length=20,
        choices=WorkflowType.choices,
        default=WorkflowType.WHOLE_BLOOD,
        verbose_name='Donation Type',
        db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='workflows_created')
    
    donation_code = models.CharField(max_length=50, unique=True, null=True, blank=True)

    def __str__(self):
        return f"{self.donor.full_name} - {self.get_status_display()}"

    def get_workflow_steps(self):
        """Return ordered steps for all instances."""
        return [
            self.Step.REGISTRATION,
            self.Step.QUESTIONNAIRE,
            self.Step.MEDICATION,
            self.Step.VITALS,
            self.Step.COLLECTION,
            self.Step.POST_DONATION,
            self.Step.ADVERSE_REACTION,
            self.Step.SURVEY,
            self.Step.DEFERRED,
            self.Step.PRE_SEPARATION,
            self.Step.COMPONENTS,
            self.Step.ATTACHMENT,
            self.Step.LABEL,
            self.Step.LABS,
            self.Step.SELF_EXCLUSION,
            self.Step.STATUS_HISTORY,
            self.Step.COMPLETED,
        ]

    def get_current_step_index(self):
        """Return index of current status in ordered steps."""
        steps = self.get_workflow_steps()
        try:
            return steps.index(self.status)
        except ValueError:
            return 0

class EligibilityRule(models.Model):
    class Category(models.TextChoices):
        DEMOGRAPHICS = 'DEMOGRAPHICS', 'Demographics'
        VITALS = 'VITALS', 'Vital Signs'
        LABS = 'LABS', 'Laboratory'
        OTHER = 'OTHER', 'Other'

    class Gender(models.TextChoices):
        MALE = 'M', 'Male'
        FEMALE = 'F', 'Female'
        ANY = 'ANY', 'Any'

    name = models.CharField(max_length=100)
    key = models.SlugField(max_length=50, unique=True, help_text="Unique key for code lookup e.g. 'bp_systolic'")
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.VITALS)
    
    # Validation Params
    min_value = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    max_value = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    
    # Target
    gender = models.CharField(max_length=5, choices=Gender.choices, default=Gender.ANY)
    
    # Action
    is_active = models.BooleanField(default=True)
    deferral_code = models.CharField(max_length=50, blank=True, null=True, help_text="Code for auto-generated Deferral Reason e.g. 'VITAL_FAIL'")
    is_permanent_deferral = models.BooleanField(default=False)
    deferral_days = models.IntegerField(default=0, help_text="Days to defer. Ignored if permanent.")
    
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_gender_display()})"


class QuestionnaireResponse(models.Model):
    workflow = models.OneToOneField(DonorWorkflow, on_delete=models.CASCADE, related_name='questionnaire')
    answers = models.JSONField(default=dict) # Store Q1:Yes, Q2:No, etc.
    signature_data = models.TextField(null=True, blank=True) # Base64 signature
    additional_notes = models.TextField(null=True, blank=True)
    passed = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Question(models.Model):
    text_en = models.CharField(max_length=500)
    text_ar = models.CharField(max_length=500)
    category = models.CharField(max_length=100, default='General Health', help_text="Type Category Name (e.g. General, Travel)")
    is_active = models.BooleanField(default=True)
    order = models.IntegerField(default=0)
    
    # Simple rule logic: "Is YES the dangerous answer?" or similar.
    # For V3, let's explicitely define the "Deferral Answer"
    defer_if_answer_is = models.CharField(max_length=3, choices=[('Yes', 'Yes'), ('No', 'No')], default='Yes', help_text="If user answers this, they are deferred.")
    deferral_days = models.IntegerField(default=0, help_text="0 = Permanent Deferral, >0 = Temporary")

    def __str__(self):
        return self.text_en[:50]

class VitalSigns(models.Model):
    workflow = models.OneToOneField(DonorWorkflow, on_delete=models.CASCADE, related_name='vitals')
    
    weight_kg = models.DecimalField(max_digits=5, decimal_places=2)
    bp_systolic = models.IntegerField()
    bp_diastolic = models.IntegerField()
    pulse = models.IntegerField()
    temperature_c = models.DecimalField(max_digits=4, decimal_places=1)
    hemoglobin = models.DecimalField(max_digits=4, decimal_places=1)
    
    iqama_checked = models.BooleanField(default=False)
    
    passed = models.BooleanField(default=False)
    examiner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)



class LabOrder(models.Model):
    class Status(models.TextChoices):
        SENT = 'SENT', 'Sent'
        RECEIVED = 'RECEIVED', 'Received'
        ERROR = 'ERROR', 'Error'

    workflow = models.ForeignKey(DonorWorkflow, on_delete=models.CASCADE, related_name='lab_orders')
    order_code = models.CharField(max_length=50, unique=True)
    system = models.CharField(max_length=50, help_text="e.g. 'Infinity', 'Ortho'")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SENT)
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"{self.order_code} ({self.system})"

class LabResult(models.Model):
    workflow = models.ForeignKey(DonorWorkflow, on_delete=models.CASCADE, related_name='lab_results')
    
    test_code = models.CharField(max_length=50) # e.g. '2026'
    test_name = models.CharField(max_length=100) # e.g. 'HBsAg'
    
    result_value = models.CharField(max_length=100) # e.g. '0.12', 'Reactive'
    is_abnormal = models.BooleanField(default=False)
    
    units = models.CharField(max_length=20, blank=True, null=True)
    reference_range = models.CharField(max_length=50, blank=True, null=True)
    
    technician = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    tested_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.test_name}: {self.result_value}"

class VitalLimit(models.Model):
    """
    Singleton model to store vital sign limits.
    """
    min_weight_kg = models.DecimalField(max_digits=5, decimal_places=2, default=50.00)
    min_hemoglobin = models.DecimalField(max_digits=4, decimal_places=1, default=12.5)
    max_temperature_c = models.DecimalField(max_digits=4, decimal_places=1, default=37.5)
    
    # Blood Pressure
    max_systolic = models.IntegerField(default=180)
    min_systolic = models.IntegerField(default=90)
    max_diastolic = models.IntegerField(default=100)
    min_diastolic = models.IntegerField(default=50)
    
    # Pulse
    max_pulse = models.IntegerField(default=100)
    min_pulse = models.IntegerField(default=50)

    def save(self, *args, **kwargs):
        # Enforce singleton
        if not self.pk and VitalLimit.objects.exists():
            return VitalLimit.objects.first()
        return super(VitalLimit, self).save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

    class Meta:
        verbose_name_plural = "Vital Settings"

class BloodDraw(models.Model):
    workflow = models.OneToOneField('DonorWorkflow', on_delete=models.CASCADE, related_name='blood_draw')
    examiner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    # Pre-checks
    bag_visual_inspection = models.BooleanField(default=False)
    iqama_checked = models.BooleanField(default=False)
    both_arm_inspection = models.BooleanField(default=False)
    
    # Site
    arm = models.CharField(max_length=10, choices=[('Right', 'Right'), ('Left', 'Left')], blank=True)
    
    # Details
    blood_type = models.CharField(max_length=10, blank=True)
    blood_nature = models.CharField(max_length=50, default='Whole Blood')
    first_unit_volume = models.IntegerField(null=True, blank=True)
    
    drawn_start_time = models.TimeField(null=True, blank=True)
    drawn_end_time = models.TimeField(null=True, blank=True)
    apheresis_start_time = models.TimeField(null=True, blank=True)
    apheresis_end_time = models.TimeField(null=True, blank=True)
    
    segment_number = models.CharField(max_length=50, blank=True)
    
    # --- Apheresis Specific Fields --- #
    procedure_type = models.CharField(max_length=50, blank=True, null=True) # e.g., PLT
    is_filtered = models.BooleanField(default=False)
    total_acd_used = models.IntegerField(null=True, blank=True)
    actual_acd_to_donor = models.IntegerField(null=True, blank=True)
    post_platelet_count = models.IntegerField(null=True, blank=True)
    post_hct = models.IntegerField(null=True, blank=True)
    blood_volume_processed = models.IntegerField(null=True, blank=True)
    total_saline_used = models.IntegerField(null=True, blank=True)
    
    kit_lot_no = models.CharField(max_length=50, blank=True, null=True)
    kit_lot_expiry = models.DateField(null=True, blank=True)
    acd_lot_no = models.CharField(max_length=50, blank=True, null=True)
    acd_lot_expiry = models.DateField(null=True, blank=True)
    
    machine_name = models.CharField(max_length=100, blank=True, null=True)
    platelets_collected_volume = models.IntegerField(null=True, blank=True)
    yield_of_platelets = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    volume_of_acd_in_platelets = models.IntegerField(null=True, blank=True)
    inventory_units_count = models.IntegerField(default=1)
    
    pre_platelet_count = models.IntegerField(null=True, blank=True)
    donation_reaction = models.BooleanField(default=False)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def duration_minutes(self):
        if self.drawn_start_time and self.drawn_end_time:
            import datetime
            # Combine with dummy date to subtract
            t1 = datetime.datetime.combine(datetime.date.today(), self.drawn_start_time)
            t2 = datetime.datetime.combine(datetime.date.today(), self.drawn_end_time)
            if t2 < t1: # Handle overnight (unlikely for donations but good practice)
                t2 += datetime.timedelta(days=1)
            diff = t2 - t1
            return int(diff.total_seconds() / 60)
        return None

    def __str__(self):
        return f"Blood Draw for {self.workflow}"

class ComponentDefinition(models.Model):
    name = models.CharField(max_length=100, unique=True)
    deferral_days = models.IntegerField(default=0, help_text="Days to defer donor after taking this medication. 0 = Permanent.")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.deferral_days} days)"

class Medication(models.Model):
    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=100, default='General', help_text="Grouping for the donor portal.")
    deferral_days = models.IntegerField(default=0, help_text="Days to defer donor after taking this medication. 0 = Permanent.")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.category}: {self.name} ({self.deferral_days} days)"

class DeferralReason(models.Model):
    class DeferralType(models.TextChoices):
        REGULAR = 'REGULAR', 'Regular'
        MEDICATION = 'MEDICATION', 'Medication'
        DISEASE = 'DISEASE', 'Disease'
        TRAVEL = 'TRAVEL', 'Travel'

    code = models.CharField(max_length=20, unique=True, help_text="Short code e.g. 'LOW_HB'")
    reason_en = models.CharField(max_length=200, verbose_name="Title (EN)")
    reason_ar = models.CharField(max_length=200, verbose_name="Title (AR)")
    
    description_en = models.TextField(blank=True, null=True, verbose_name="Description (EN)")
    description_ar = models.TextField(blank=True, null=True, verbose_name="Description (AR)")
    
    is_permanent = models.BooleanField(default=False)
    default_duration_days = models.IntegerField(default=0, help_text="Blocking Days")
    
    deferral_type = models.CharField(max_length=20, choices=DeferralType.choices, default=DeferralType.REGULAR)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.reason_en} ({self.code})"

class CollectionConfig(models.Model):
    """
    Singleton model for Blood Collection Settings.
    """
    default_volume_ml = models.IntegerField(default=450)
    min_volume_ml = models.IntegerField(default=400)
    max_volume_ml = models.IntegerField(default=500)
    
    min_duration_minutes = models.IntegerField(default=5, help_text="Minimum donation time before alerting flow issues.")
    max_duration_minutes = models.IntegerField(default=15, help_text="Maximum duration before considering partial.")

    # Workflow Control
    enable_pre_donation_checks = models.BooleanField(default=True, verbose_name="Enable Pre-Donation Checks")
    require_bag_inspection = models.BooleanField(default=True, verbose_name="Require Bag Visual Inspection")
    require_arm_inspection = models.BooleanField(default=True, verbose_name="Require Both Arms Inspection")
    allow_manual_time_entry = models.BooleanField(default=False, verbose_name="Allow Manual Time Entry")
    
    # Stages Enable/Disable
    enable_separation_stage = models.BooleanField(default=True, verbose_name="Enable Separation Stage")
    enable_initial_label_stage = models.BooleanField(default=True, verbose_name="Enable Initial Label")
    enable_untested_storage_stage = models.BooleanField(default=True, verbose_name="Enable Un-Tested Storage")
    enable_notifications_stage = models.BooleanField(default=True, verbose_name="Enable Notifications")

    def save(self, *args, **kwargs):
        if not self.pk and CollectionConfig.objects.exists():
            return CollectionConfig.objects.first()
        return super(CollectionConfig, self).save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

    class Meta:
        verbose_name_plural = "Blood Collection Settings"

class ProductSeparationRule(models.Model):
    class Component(models.TextChoices):
        WHOLE_BLOOD = 'WB', 'Whole Blood'
        PACKED_RBC = 'RBC', 'Packed Red Blood Cells'
        PLASMA_FFP = 'FFP', 'Fresh Frozen Plasma'
        PLASMA_LIQUID = 'LP', 'Liquid Plasma'
        PLATELETS_RANDOM = 'PLT', 'Platelets (Random)'
        PLATELETS_APHERESIS = 'PLT_APH', 'Platelets (Apheresis)'
        CRYOPRECIPITATE = 'CRYO', 'Cryoprecipitate'
        GRANULOCYTES = 'GRAN', 'Granulocytes'

    name = models.CharField(max_length=100, help_text="Internal name e.g. 'Standard Leukoreduced RBC'")
    component_type = models.CharField(max_length=10, choices=Component.choices, default=Component.PACKED_RBC)
    
    # Validation Constraints for the source pack
    min_volume_ml = models.IntegerField(default=400, help_text="Min source volume required")
    max_volume_ml = models.IntegerField(default=550, help_text="Max source volume allowed")
    
    # Process Params
    centrifuge_program = models.CharField(max_length=100, blank=True, help_text="e.g. 'Hard Spin 5000rpm 5min'")
    expiration_hours = models.IntegerField(default=42*24, help_text="Shelf life in hours from collection.")
    
    is_active = models.BooleanField(default=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_component_type_display()})"

class AdverseReaction(models.Model):
    class Severity(models.TextChoices):
        MILD = 'MILD', 'Mild'
        MODERATE = 'MODERATE', 'Moderate'
        SEVERE = 'SEVERE', 'Severe'

    workflow = models.OneToOneField('DonorWorkflow', on_delete=models.CASCADE, related_name='adverse_reaction')
    
    # Types of reactions
    is_faint = models.BooleanField(default=False, verbose_name="Faint / Dizziness")
    is_hematoma = models.BooleanField(default=False, verbose_name="Hematoma / Bruise")
    is_nerve_injury = models.BooleanField(default=False, verbose_name="Nerve Injury")
    is_arterial_puncture = models.BooleanField(default=False, verbose_name="Arterial Puncture")
    is_citrate_reaction = models.BooleanField(default=False, verbose_name="Citrate Reaction")
    is_other = models.BooleanField(default=False, verbose_name="Other")
    
    other_description = models.CharField(max_length=200, blank=True, null=True)
    
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.MILD)
    
    # Timing
    onset_time = models.TimeField(null=True, blank=True)
    ended_time = models.TimeField(null=True, blank=True)
    
    # Management
    management_notes = models.TextField(blank=True, help_text="What was done? (Ice pack, fluids, leg raise)")
    
    examiner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Reaction for {self.workflow} ({self.severity})"

class ModificationRequest(models.Model):
    class RequestType(models.TextChoices):
        COMPONENT = 'COMPONENT', 'Component'
        DONATION = 'DONATION', 'Donation'
        SETTINGS = 'SETTINGS', 'Settings'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
        DONE = 'DONE', 'Done'

    request_type = models.CharField(max_length=20, choices=RequestType.choices, default=RequestType.COMPONENT)
    reference_code = models.CharField(max_length=50, help_text="DIN or Entity ID")
    
    # Text blob to describe changes like "Volume: 450 -> 300"
    modification_details = models.TextField(help_text="Description of changes") 
    
    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    
    created_at = models.DateTimeField(auto_now_add=True)
    # Using string reference to avoid import loops if User model is customized, though AUTH_USER_MODEL is safer
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='mod_requests_created')
    
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='mod_requests_completed')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.reference_code} - {self.request_type}"

class PostDonationSurvey(models.Model):
    class Satisfaction(models.IntegerChoices):
        VERY_DISSATISFIED = 1, 'Very Dissatisfied'
        DISSATISFIED = 2, 'Dissatisfied'
        NEUTRAL = 3, 'Neutral'
        SATISFIED = 4, 'Satisfied'
        VERY_SATISFIED = 5, 'Very Satisfied'

    workflow = models.OneToOneField('DonorWorkflow', on_delete=models.CASCADE, related_name='survey')
    
    # Questions based on screenshots
    comfort_during_process = models.IntegerField(choices=Satisfaction.choices, default=Satisfaction.VERY_SATISFIED, verbose_name="Comfort during donation")
    staff_satisfaction = models.IntegerField(choices=Satisfaction.choices, default=Satisfaction.VERY_SATISFIED, verbose_name="Satisfaction with Team")
    wait_time_satisfaction = models.IntegerField(choices=Satisfaction.choices, default=Satisfaction.VERY_SATISFIED, verbose_name="Satisfaction with Wait Time")
    
    comments = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Survey for {self.workflow}"

class PreSeparation(models.Model):
    class BagType(models.TextChoices):
        DOUBLE_FILTER = 'Double_Filter', 'Double Filter'
        TRIPLE_FILTER = 'Triple_Filter', 'Triple Filter'
        QUADRUPLE_FILTER = 'Quadruple_Filter', 'Quadruple Filter'
        SINGLE = 'Single', 'Single'

    workflow = models.OneToOneField('DonorWorkflow', on_delete=models.CASCADE, related_name='pre_separation')
    
    # Checkboxes
    unit_label_check = models.BooleanField(default=False, verbose_name="Unit Label Check?")
    donor_sample_label_check = models.BooleanField(default=False, verbose_name="Donor Sample Label Checked?")
    unit_temp_check = models.BooleanField(default=False, verbose_name="Unit Temp Checked?")
    visual_inspection = models.BooleanField(default=False, verbose_name="Visual Inspection?")
    
    # Measurements
    volume_ml = models.IntegerField(null=True, blank=True, verbose_name="Volume")
    
    # Bag Info
    bag_lot_no = models.CharField(max_length=50, blank=True, null=True, verbose_name="Bag Lot No")
    bag_type = models.CharField(max_length=50, choices=BagType.choices, default=BagType.DOUBLE_FILTER, verbose_name="Bag Type")
    bag_expiry_date = models.DateField(null=True, blank=True, verbose_name="Bag Expiry Date")
    
    # Notes
    notes = models.TextField(blank=True, null=True, verbose_name="Notes")

    # Calculated / Snapshot data
    donation_ended_at = models.TimeField(null=True, blank=True) # Snapshot from BloodDraw
    received_at = models.DateTimeField(null=True, blank=True) # When received in lab
    
    # User Inputs (Previous fields, keeping for compatibility if needed or removing if replaced)
    extract_type = models.CharField(max_length=100, default='All Components Can be Extracted')
    
    # Status
    received_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='received_pre_separations')
    
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='verified_pre_separations')
    verified_at = models.DateTimeField(null=True, blank=True)
    
    is_approved = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Pre-Separation for {self.workflow}"

class DonationAttachment(models.Model):
    workflow = models.ForeignKey(DonorWorkflow, on_delete=models.CASCADE, related_name='attachments')
    title = models.CharField(max_length=200)
    notes = models.TextField(blank=True, null=True)
    file = models.FileField(upload_to='attachments/%Y/%m/')
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='uploaded_attachments')

    def __str__(self):
        return f"{self.title} ({self.file.name})"


class PostDonationCare(models.Model):
    """Records post-donation care given to donor after blood collection."""
    workflow = models.OneToOneField(
        'DonorWorkflow', on_delete=models.CASCADE, related_name='post_donation_care'
    )
    # Observations
    felt_dizzy = models.BooleanField(default=False, verbose_name="Felt Dizzy?")
    felt_nauseous = models.BooleanField(default=False, verbose_name="Felt Nauseous?")
    felt_faint = models.BooleanField(default=False, verbose_name="Fainted?")
    felt_well = models.BooleanField(default=True, verbose_name="Felt Well?")

    # Care given
    given_refreshments = models.BooleanField(default=False, verbose_name="Refreshments Given?")
    given_rest = models.BooleanField(default=False, verbose_name="Rest Given?")
    applied_bandage = models.BooleanField(default=True, verbose_name="Bandage Applied?")

    # Vitals post-donation
    post_bp_systolic = models.IntegerField(null=True, blank=True, verbose_name="Post BP Systolic")
    post_bp_diastolic = models.IntegerField(null=True, blank=True, verbose_name="Post BP Diastolic")
    post_pulse = models.IntegerField(null=True, blank=True, verbose_name="Post Pulse")

    # Discharge
    rest_duration_minutes = models.IntegerField(default=15, verbose_name="Rest Duration (min)")
    discharge_time = models.TimeField(null=True, blank=True, verbose_name="Discharge Time")
    donor_instructions_given = models.BooleanField(default=True, verbose_name="Instructions Given?")

    notes = models.TextField(blank=True, null=True)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Post-Donation Care for {self.workflow}"


class DonorMedicationRecord(models.Model):
    """Records medications declared by donor during the Medication Review step."""
    workflow = models.OneToOneField(
        'DonorWorkflow', on_delete=models.CASCADE, related_name='medication_record'
    )
    is_on_medication = models.BooleanField(default=False, verbose_name="Is Donor on Any Medication?")
    medications_taken = models.ManyToManyField(
        Medication, blank=True, verbose_name="Medications Listed"
    )
    other_medication_notes = models.TextField(
        blank=True, null=True, verbose_name="Other / Free-text Medications"
    )
    pharmacist_reviewed = models.BooleanField(default=False, verbose_name="Reviewed by Pharmacist?")
    deferred_due_to_medication = models.BooleanField(
        default=False, verbose_name="Deferred Due to Medication?"
    )
    notes = models.TextField(blank=True, null=True)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Medication Record for {self.workflow}"


class BloodUnitCulture(models.Model):
    """Bacterial Culture Results for Blood Units (Platelets, PRBC, etc.)"""
    class CultureStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        NEGATIVE = 'NEGATIVE', 'Negative'
        POSITIVE = 'POSITIVE', 'Positive'

    class FinalInterpretation(models.TextChoices):
        STERILE = 'STERILE', 'Sterile Blood Component'
        CONTAMINATED = 'CONTAMINATED', 'Bacterial Contamination'
        PENDING = 'PENDING', 'Pending'

    class UnitStatus(models.TextChoices):
        RELEASED = 'RELEASED', 'Released for Transfusion'
        QUARANTINED = 'QUARANTINED', 'Quarantined'
        DISCARDED = 'DISCARDED', 'Discarded'

    workflow = models.ForeignKey('DonorWorkflow', on_delete=models.CASCADE, related_name='culture_results')
    component_name = models.CharField(max_length=100, default='Platelet Unit')
    sample_type = models.CharField(max_length=100, default='Platelet Concentrate')
    
    collection_date = models.DateField(null=True, blank=True)
    report_date = models.DateField(null=True, blank=True)
    
    status = models.CharField(max_length=20, choices=CultureStatus.choices, default=CultureStatus.PENDING)
    incubation_days = models.IntegerField(default=7, help_text="Incubation period in days")
    current_incubation_day = models.IntegerField(default=1)
    
    growth_status = models.CharField(max_length=100, default='No Growth Yet')
    final_interpretation = models.CharField(max_length=100, choices=FinalInterpretation.choices, default=FinalInterpretation.PENDING)
    unit_status = models.CharField(max_length=50, choices=UnitStatus.choices, default=UnitStatus.QUARANTINED)
    
    # Conditional fields for Positive results
    organism_name = models.CharField(max_length=200, blank=True, null=True) # e.g. Staphylococcus epidermidis
    gram_stain_result = models.CharField(max_length=200, blank=True, null=True) # e.g. Gram Positive Cocci
    colony_count = models.CharField(max_length=100, blank=True, null=True) # e.g. >100 CFU/mL
    antibiotic_susceptibility = models.TextField(blank=True, null=True)
    comments = models.TextField(blank=True, null=True)
    
    approved_by_name = models.CharField(max_length=150, default='Dr. Ahmed (Microbiologist)')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_cultures')
    approval_time = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Culture Result for {self.workflow} - {self.status}"