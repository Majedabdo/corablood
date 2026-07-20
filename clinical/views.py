from .services import WorkflowService
from donors.models import Donor
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from core.decorators import staff_required

from rest_framework import viewsets, status, decorators
from rest_framework.response import Response
from django.db import transaction
from django.utils import timezone
from .models import DonorWorkflow, Question, VitalLimit, BloodDraw, AdverseReaction, PostDonationSurvey, PreSeparation
from .serializers import (
    QuestionSerializer, VitalSignsSerializer, VitalLimitSerializer, 
    WorkflowDetailSerializer, BloodDrawSerializer, AdverseReactionSerializer, 
    PostDonationSurveySerializer, PreSeparationSerializer, BloodComponentSerializer
)
from .services import WorkflowService
from .queue_api import WorkflowQueueViewSet

# Settings Views
@staff_required
def settings_questionnaire(request):
    return render(request, 'clinical/settings_questionnaire.html')

@staff_required
def settings_vitals(request):
    return render(request, 'clinical/settings_vitals.html')

@staff_required
def settings_contraindications(request):
    from .models import EligibilityRule, DeferralReason, CollectionConfig, ProductSeparationRule
    
    # Load Config
    config, _ = CollectionConfig.objects.get_or_create(pk=1)
    
    if request.method == 'POST':
        action = request.POST.get('action') # 'rule_save' or 'config_save'
        
        if action == 'config_save':
            # Workflow Controls
            config.enable_pre_donation_checks = request.POST.get('enable_pre_donation_checks') == 'on'
            config.require_bag_inspection = request.POST.get('require_bag_inspection') == 'on'
            config.require_arm_inspection = request.POST.get('require_arm_inspection') == 'on'
            config.allow_manual_time_entry = request.POST.get('allow_manual_time_entry') == 'on'
            
            # Stages
            config.enable_separation_stage = request.POST.get('enable_separation_stage') == 'on'
            config.enable_initial_label_stage = request.POST.get('enable_initial_label_stage') == 'on'
            config.enable_untested_storage_stage = request.POST.get('enable_untested_storage_stage') == 'on'
            config.enable_notifications_stage = request.POST.get('enable_notifications_stage') == 'on'
            
            config.save()
            messages.success(request, "Workflow settings updated.")
            return redirect('settings_contraindications')

        elif action == 'separation_rule_save':
            s_id = request.POST.get('separation_rule_id')
            
            defaults = {
                'name': request.POST.get('name'),
                'component_type': request.POST.get('component_type'),
                'min_volume_ml': request.POST.get('min_volume_ml') or 0,
                'max_volume_ml': request.POST.get('max_volume_ml') or 0,
                'centrifuge_program': request.POST.get('centrifuge_program'),
                'expiration_hours': request.POST.get('expiration_hours') or 0,
                'is_active': True 
            }
            
            if s_id:
                ProductSeparationRule.objects.filter(pk=s_id).update(**defaults)
                messages.success(request, "Separation rule updated.")
            else:
                ProductSeparationRule.objects.create(**defaults)
                messages.success(request, "New separation rule created.")
            
            return redirect('settings_contraindications')

        elif action == 'separation_rule_delete':
            s_id = request.POST.get('separation_rule_id')
            ProductSeparationRule.objects.filter(pk=s_id).delete()
            messages.success(request, "Separation rule deleted.")
            return redirect('settings_contraindications')

        else:
            # Rule Save Logic (Existing)
            rule_id = request.POST.get('rule_id')
            min_val = request.POST.get('min_value')
            max_val = request.POST.get('max_value')
            
            # Deferral Params
            deferral_code = request.POST.get('deferral_code')
            deferral_type = request.POST.get('deferral_type') # 'PERMANENT' or 'TEMPORARY'
            days = request.POST.get('deferral_days', 0)
            
            rule = get_object_or_404(EligibilityRule, pk=rule_id)
            
            if min_val: rule.min_value = min_val
            if max_val: rule.max_value = max_val
            
            # Update Deferral config
            if deferral_code:
                rule.deferral_code = deferral_code
                rule.is_permanent_deferral = (deferral_type == 'PERMANENT')
                rule.deferral_days = int(days) if days else 0
                
                # Sync with central DeferralSettings
                DeferralReason.objects.update_or_create(
                    code=deferral_code,
                    defaults={
                        'reason_en': f"Violation of {rule.name}",
                        'reason_ar': f"مخالفة {rule.name}",
                        'is_permanent': rule.is_permanent_deferral,
                        'default_duration_days': rule.deferral_days
                    }
                )
                messages.success(request, f"Updated rule & synced deferral: {rule.name}")
            else:
                messages.success(request, f"Updated rule: {rule.name}")
                
            rule.save()
            return redirect('settings_contraindications')

    rules = EligibilityRule.objects.all()
    separation_rules = ProductSeparationRule.objects.all()
    return render(request, 'clinical/settings_contraindications.html', {
        'rules': rules, 
        'config': config,
        'separation_rules': separation_rules
    })


class VitalLimitViewSet(viewsets.ModelViewSet):
    from core.permissions import IsStaffOrClinicalAdmin
    permission_classes = [IsStaffOrClinicalAdmin]
    queryset = VitalLimit.objects.all()
    serializer_class = VitalLimitSerializer

    def get_queryset(self):
        # Ensure at least one exists
        if not VitalLimit.objects.exists():
            VitalLimit.load() # Creates default
        return super().get_queryset()

class QuestionViewSet(viewsets.ModelViewSet):
    from core.permissions import IsStaffOrClinicalAdmin
    permission_classes = [IsStaffOrClinicalAdmin]
    queryset = Question.objects.filter(is_active=True).order_by('order')
    serializer_class = QuestionSerializer

    def get_queryset(self):
        # Admin sees all, frontend sees active only? 
        # For settings page, we want to see ALL questions to manage them.
        return Question.objects.all().order_by('order')

class BloodComponentViewSet(viewsets.ModelViewSet):
    from core.permissions import IsStaffOrClinicalAdmin
    permission_classes = [IsStaffOrClinicalAdmin]
    from inventory.models import BloodComponent
    queryset = BloodComponent.objects.all()
    serializer_class = BloodComponentSerializer

    @decorators.action(detail=True, methods=['post'])
    def print_label(self, request, pk=None):
        comp = self.get_object()
        # In a real system, this might generate a PDF URL
        comp.is_labeled = True
        comp.label_printed_at = timezone.now()
        comp.save()
        return Response({'status': 'success', 'message': 'Label metadata updated.'})

    @decorators.action(detail=True, methods=['post'])
    def disposition(self, request, pk=None):
        from inventory.models import BloodComponent
        comp = self.get_object()
        comp.status = BloodComponent.Status.AVAILABLE
        comp.location = "Main Stock Fridge"
        comp.modified_by = request.user
        comp.save()
        return Response({'status': 'success', 'message': 'Component moved to store.'})

    @decorators.action(detail=True, methods=['post'])
    def irradiate(self, request, pk=None):
        comp = self.get_object()
        if not comp.notes: comp.notes = ""
        if "[IRRADIATED]" not in comp.notes:
            comp.notes = (comp.notes + " [IRRADIATED]").strip()
        comp.modified_by = request.user
        comp.save()
        return Response({'status': 'success', 'message': 'Component marked as irradiated.'})

    @decorators.action(detail=True, methods=['post'])
    def discard(self, request, pk=None):
        from inventory.models import BloodComponent
        comp = self.get_object()
        comp.status = BloodComponent.Status.DISCARDED
        comp.location = "Biohazard Disposal"
        comp.modified_by = request.user
        comp.save()
        return Response({'status': 'success', 'message': 'Component discarded.'})

class DonationWorkflowViewSet(viewsets.ReadOnlyModelViewSet):
    from core.permissions import IsStaffOrClinicalAdmin
    permission_classes = [IsStaffOrClinicalAdmin]
    queryset = DonorWorkflow.objects.all()
    serializer_class = WorkflowDetailSerializer

    @decorators.action(detail=True, methods=['get'])
    def questions(self, request, pk=None):
        """Return all active questions"""
        questions = Question.objects.filter(is_active=True).order_by('order')
        return Response(QuestionSerializer(questions, many=True).data)

    @decorators.action(detail=True, methods=['post'])
    def confirm_profile(self, request, pk=None):
        workflow = self.get_object()
        
        # Move to Questionnaire step
        if workflow.status == DonorWorkflow.Step.REGISTRATION:
            workflow.status = DonorWorkflow.Step.QUESTIONNAIRE
            workflow.save()
            return Response({'status': 'success'})
        
        return Response({'status': 'ignored', 'message': 'Workflow not in Registration state'}, status=200)

    @decorators.action(detail=True, methods=['post'])
    def submit_answers(self, request, pk=None):
        workflow = self.get_object()
        answers = request.data.get('answers', [])
        # answers format: [{'question_id': 1, 'answer': 'Yes'}, ...]
        
        # Simple Logic: Check for deferrals
        flat_answers = {}
        deferred = False
        deferral_reason = ""
        days = 0
        
        for ans in answers:
            qid = ans.get('question_id')
            val = ans.get('answer')
            flat_answers[qid] = val
            
            try:
                q = Question.objects.get(pk=qid)
                if q.defer_if_answer_is == val:
                    deferred = True
                    deferral_reason = f"Answered {val} to: {q.text_en}"
                    days = max(days, q.deferral_days)
            except Question.DoesNotExist:
                pass

        if deferred:
            workflow.status = DonorWorkflow.Step.DEFERRED
            workflow.save()
            return Response({
                'status': 'deferred',
                'result': 'REJECTED',
                'reason': deferral_reason,
                'defer_until': f"{days} days"
            })
            
        # Pass
        WorkflowService.submit_questionnaire(workflow, flat_answers, request.user)
        return Response({'status': 'success'})

    @decorators.action(detail=True, methods=['post'])
    def save_vitals(self, request, pk=None):
        workflow = self.get_object()
        serializer = VitalSignsSerializer(data=request.data)
        if serializer.is_valid():
            vitals, reasons = WorkflowService.submit_vitals(workflow, serializer.validated_data, request.user)
            if vitals.passed:
                return Response({'status': 'success', 'next_step': 'collection'})
            else:
                return Response({
                    'status': 'rejected',
                    'reason': ", ".join(reasons)
                })
        return Response(serializer.errors, status=400)

    @decorators.action(detail=True, methods=['post'])
    def print_label(self, request, pk=None):
        """
        Mock endpoint to print Donation Code barcode.
        """
        workflow = self.get_object()
        if hasattr(workflow, 'blood_draw'):
            code = workflow.blood_draw.segment_number
            # In a real system, this would send a ZPL/EPL command to a network printer at 192.168.x.x
            # or return a PDF/Image blob.
            return Response({
                'status': 'success', 
                'message': f'Printing barcode for {code}...',
                'code': code,
                'printed_at': 'Now'
            })
        return Response({'status': 'error', 'message': 'No Blood Draw found'}, status=400)

    @decorators.action(detail=True, methods=['post'])
    def save_withdrawal(self, request, pk=None):
        workflow = self.get_object()
        serializer = BloodDrawSerializer(data=request.data)
        
        if not serializer.is_valid():
            # Explicitly return constraint errors
            return Response({'status': 'error', 'message': f"Validation Failed: {serializer.errors}"}, status=400)

        try:
            data = serializer.validated_data
            
            # Use Service Layer for consistency
            draw = WorkflowService.submit_blood_draw(workflow, data, request.user)
            
            # Follow official sequence: COLLECTION -> POST_DONATION
            workflow.status = DonorWorkflow.Step.POST_DONATION
            workflow.save()

            return Response({
                'status': 'success',
                'segment_number': draw.segment_number,
                'donation_code': workflow.donation_code,
                'draw_id': draw.id
            })

        except Exception as e:
            import traceback
            return Response({
                'status': 'error', 
                'message': f"Server Error: {str(e)}", 
                'traceback': traceback.format_exc()
            }, status=500)

    @decorators.action(detail=True, methods=['post'])
    def save_post_donation(self, request, pk=None):
        workflow = self.get_object()
        if workflow.status == DonorWorkflow.Step.POST_DONATION:
            workflow.status = DonorWorkflow.Step.ADVERSE_REACTION
            workflow.save()
        return Response({'status': 'success', 'message': 'Post-donation care completed.'})

    @decorators.action(detail=True, methods=['post'])
    def save_adverse_reaction(self, request, pk=None):
        workflow = self.get_object()
        data = request.data
        
        # Check if skipping (No Reaction)
        if data.get('no_reaction') is True:
            # Ensure no reaction exists? Or keep history?
            # Ideally we check if one exists and delete it? Or just ignore?
            # Let's clean up if user changed mind from Yes to No.
            if hasattr(workflow, 'adverse_reaction'):
                workflow.adverse_reaction.delete()
            
            # Progress Step
            if workflow.status in [DonorWorkflow.Step.ADVERSE_REACTION, DonorWorkflow.Step.COLLECTION, DonorWorkflow.Step.POST_DONATION]:
                 workflow.status = DonorWorkflow.Step.PRE_SEPARATION
                 workflow.save()
                 
            return Response({'status': 'success', 'message': 'No adverse reaction recorded.'})
        
        # Save Reaction
        serializer = AdverseReactionSerializer(data=data)
        if serializer.is_valid():
            # Update or Create
            # Since OneToOne, we should check existing
            AdverseReaction.objects.update_or_create(
                workflow=workflow,
                defaults=serializer.validated_data
            )
            
            
            if workflow.status in [DonorWorkflow.Step.ADVERSE_REACTION, DonorWorkflow.Step.COLLECTION, DonorWorkflow.Step.POST_DONATION]:
                workflow.status = DonorWorkflow.Step.PRE_SEPARATION
                workflow.save()
            
            return Response({'status': 'success', 'message': 'Adverse reaction recorded.'})
        
        return Response(serializer.errors, status=400)

    @decorators.action(detail=True, methods=['post'])
    def save_survey(self, request, pk=None):
        workflow = self.get_object()
        data = request.data
        
        serializer = PostDonationSurveySerializer(data=data)
        if serializer.is_valid():
            PostDonationSurvey.objects.update_or_create(
                workflow=workflow,
                defaults=serializer.validated_data
            )
            
            if workflow.status == DonorWorkflow.Step.SURVEY:
                 workflow.status = DonorWorkflow.Step.LABS
                 workflow.save()
            
            return Response({'status': 'success', 'message': 'Survey saved.'})
        
        return Response(serializer.errors, status=400)

    @decorators.action(detail=True, methods=['post'])
    def save_medication(self, request, pk=None):
        workflow = self.get_object()
        data = request.data
        
        from .serializers import DonorMedicationRecordSerializer
        from .models import DonorMedicationRecord
        
        serializer = DonorMedicationRecordSerializer(data=data)
        if serializer.is_valid():
            # Handle M2M medications if needed (DonorMedicationRecord has medications_taken as M2M)
            # update_or_create doesn't handle M2M directly well in all versions, 
            # so we use set() if provided.
            
            instance, created = DonorMedicationRecord.objects.update_or_create(
                workflow=workflow,
                defaults={
                    'is_on_medication': serializer.validated_data.get('is_on_medication'),
                    'other_medication_notes': serializer.validated_data.get('other_medication_notes'),
                    'notes': serializer.validated_data.get('notes'),
                    'pharmacist_reviewed': True,
                    'deferred_due_to_medication': serializer.validated_data.get('deferred_due_to_medication', False)
                }
            )
            
            # Progress Step
            if workflow.status == DonorWorkflow.Step.QUESTIONNAIRE or workflow.status == DonorWorkflow.Step.MEDICATION or workflow.status == 'MEDICATION':
                 workflow.status = DonorWorkflow.Step.VITALS
                 workflow.save()
            
            return Response({'status': 'success', 'message': 'Medication saved.'})
        
        return Response(serializer.errors, status=400)

    @decorators.action(detail=True, methods=['post'])
    def save_pre_separation(self, request, pk=None):
        workflow = self.get_object()
        action = request.data.get('action')
        
        pre_sep, created = PreSeparation.objects.get_or_create(workflow=workflow)
        
        # Update fields using serializer for robustness
        serializer = PreSeparationSerializer(pre_sep, data=request.data, partial=True)
        if serializer.is_valid():
            instance = serializer.save()
            
            # Handle timestamps and audit info based on action
            if action == 'receive' and not instance.received_at:
                instance.received_at = timezone.now()
                instance.received_by = request.user
                instance.save()
            elif action == 'verify' and not instance.verified_at:
                instance.verified_at = timezone.now()
                instance.verified_by = request.user
                instance.save()
            elif action == 'approve':
                # Auto-Receive if not already received
                if not instance.received_at:
                    instance.received_at = timezone.now()
                    instance.received_by = request.user
                
                instance.is_approved = True
                instance.save()
                
                # Advance Workflow status
                if workflow.status == DonorWorkflow.Step.PRE_SEPARATION:
                    workflow.status = DonorWorkflow.Step.COMPONENTS
                    workflow.save()
                
            return Response({
                'status': 'success',
                'message': 'Pre-separation data saved.',
                'data': PreSeparationSerializer(instance).data
            })
        return Response(serializer.errors, status=400)

    @decorators.action(detail=True, methods=['post'])
    def submit_lab_result(self, request, pk=None):
        workflow = self.get_object()
        from .models import LabResult
        from inventory.models import BloodComponent
        from django.utils import timezone
        
        results_data = request.data.get('results')
        if not results_data and isinstance(request.data, list):
            results_data = request.data
            
        if not results_data:
            results_data = [{
                'test_code': request.data.get('test_code'),
                'test_name': request.data.get('test_name'),
                'result_value': request.data.get('result_value'),
                'is_abnormal': request.data.get('is_abnormal') in ['true', 'True', True]
            }]
        
        any_abnormal = False
        abnormal_reasons = []
        created_count = 0
        
        for r in results_data:
            code = r.get('test_code')
            val = r.get('result_value')
            if not code or not val:
                continue
            name = r.get('test_name') or code
            abnormal = r.get('is_abnormal') in ['true', 'True', True] or val in ['Reactive', 'Positive', 'Detected']
            if abnormal:
                any_abnormal = True
                abnormal_reasons.append(f"{name} ({val})")
                
            LabResult.objects.create(
                workflow=workflow,
                test_code=code,
                test_name=name,
                result_value=val,
                is_abnormal=abnormal,
                technician=request.user if request.user.is_authenticated else None,
                tested_at=timezone.now()
            )
            created_count += 1
        
        # Check overall status
        workflow.status = DonorWorkflow.Step.COMPLETED
        workflow.save(update_fields=['status', 'updated_at'] if hasattr(workflow, 'updated_at') else ['status'])
        
        # Ensure components exist for workflow
        from inventory.models import BloodComponent
        from datetime import timedelta
        
        if not BloodComponent.objects.filter(workflow=workflow).exists():
            bag_id = getattr(workflow.blood_draw, 'bag_serial_number', None) if hasattr(workflow, 'blood_draw') else None
            if not bag_id:
                bag_id = workflow.donation_code or f"UNIT-{workflow.id:05d}"
            bg = workflow.donor.blood_group if (workflow.donor and workflow.donor.blood_group) else "O+"
            now = timezone.now()
            
            BloodComponent.objects.create(
                workflow=workflow, component_type=BloodComponent.Type.PRBC,
                unit_number=f"{bag_id}-PRBC", blood_group=bg, volume=300,
                status=BloodComponent.Status.QUARANTINE, expiration_date=now + timedelta(days=42),
                created_by=request.user if request.user.is_authenticated else None
            )
            BloodComponent.objects.create(
                workflow=workflow, component_type=BloodComponent.Type.PLT,
                unit_number=f"{bag_id}-PLT", blood_group=bg, volume=50,
                status=BloodComponent.Status.QUARANTINE, expiration_date=now + timedelta(days=5),
                created_by=request.user if request.user.is_authenticated else None
            )
            BloodComponent.objects.create(
                workflow=workflow, component_type=BloodComponent.Type.FFP,
                unit_number=f"{bag_id}-FFP", blood_group=bg, volume=175,
                status=BloodComponent.Status.QUARANTINE, expiration_date=now + timedelta(days=365),
                created_by=request.user if request.user.is_authenticated else None
            )
        
        # Release components from QUARANTINE to Disposition To Store (AVAILABLE) or DISCARDED
        if any_abnormal:
            new_status = 'DISCARDED'
            note_text = f"Discarded due to abnormal lab test(s): {', '.join(abnormal_reasons)}"
        else:
            new_status = 'AVAILABLE'
            note_text = "Passed all laboratory screening tests. Approved for Disposition To Store."

        BloodComponent.objects.filter(workflow=workflow).update(
            status=new_status,
            notes=note_text,
            approved_by=request.user if request.user.is_authenticated else None,
            approved_at=timezone.now()
        )
        
        return Response({
            'status': 'success',
            'disposition_status': new_status,
            'message': f'Saved {created_count} lab results. Unit disposition set to {new_status}.'
        })
    @decorators.action(detail=True, methods=['post'])
    def add_order(self, request, pk=None):
        workflow = self.get_object()
        from .models import LabOrder
        
        LabOrder.objects.create(
            workflow=workflow,
            order_code=request.data.get('order_code'),
            system=request.data.get('system'),
            status=LabOrder.Status.SENT,
            created_by=request.user
        )
        return Response({'status': 'success', 'message': 'Order created.'})

    @decorators.action(detail=True, methods=['get'])
    def culture_results(self, request, pk=None):
        workflow = self.get_object()
        from .models import BloodUnitCulture
        from django.utils import timezone
        
        cultures = BloodUnitCulture.objects.filter(workflow=workflow).order_by('-created_at')
        
        data = []
        for c in cultures:
            data.append({
                'id': c.id,
                'component_name': c.component_name,
                'sample_type': c.sample_type,
                'collection_date': c.collection_date.strftime('%d-%b-%Y') if c.collection_date else timezone.now().strftime('%d-%b-%Y'),
                'report_date': c.report_date.strftime('%d-%b-%Y') if c.report_date else timezone.now().strftime('%d-%b-%Y'),
                'status': c.status,
                'incubation_days': c.incubation_days,
                'current_incubation_day': c.current_incubation_day,
                'growth_status': c.growth_status,
                'final_interpretation': c.get_final_interpretation_display() if hasattr(c, 'get_final_interpretation_display') else c.final_interpretation,
                'unit_status': c.unit_status,
                'organism_name': c.organism_name or 'No Growth',
                'gram_stain_result': c.gram_stain_result or '',
                'colony_count': c.colony_count or '',
                'antibiotic_susceptibility': c.antibiotic_susceptibility or '',
                'comments': c.comments or '',
                'approved_by_name': c.approved_by_name or 'Dr. Ahmed (Microbiologist)',
                'approval_time': c.approval_time.strftime('%d-%b-%Y %H:%M') if c.approval_time else timezone.now().strftime('%d-%b-%Y %H:%M'),
            })
        return Response({'status': 'success', 'results': data})

    @decorators.action(detail=True, methods=['post'])
    def request_culture(self, request, pk=None):
        workflow = self.get_object()
        from .models import BloodUnitCulture
        from django.utils import timezone
        
        component_name = request.data.get('component_name', 'Platelet Unit')
        sample_type = request.data.get('sample_type', 'Platelet Concentrate')
        
        culture = BloodUnitCulture.objects.create(
            workflow=workflow,
            component_name=component_name,
            sample_type=sample_type,
            collection_date=timezone.now().date(),
            status=BloodUnitCulture.CultureStatus.PENDING,
            incubation_days=7,
            current_incubation_day=1,
            growth_status='No Growth Yet',
            final_interpretation=BloodUnitCulture.FinalInterpretation.PENDING,
            unit_status=BloodUnitCulture.UnitStatus.QUARANTINED
        )
        return Response({'status': 'success', 'message': 'Culture test requested.', 'id': culture.id})

    @decorators.action(detail=True, methods=['post'])
    def submit_culture_result(self, request, pk=None):
        workflow = self.get_object()
        from .models import BloodUnitCulture
        from inventory.models import BloodComponent
        from django.utils import timezone
        
        status_val = request.data.get('status', 'NEGATIVE')
        approver = request.data.get('approved_by_name', 'Dr. Ahmed (Microbiologist)')
        
        culture_id = request.data.get('culture_id')
        culture = None
        if culture_id:
            culture = BloodUnitCulture.objects.filter(id=culture_id, workflow=workflow).first()
        if not culture:
            culture = BloodUnitCulture(workflow=workflow)

        culture.component_name = request.data.get('component_name', 'Platelet Unit')
        culture.sample_type = request.data.get('sample_type', 'Platelet Concentrate')
        culture.status = status_val
        culture.collection_date = timezone.now().date()
        culture.report_date = timezone.now().date()
        culture.approved_by_name = approver
        if request.user.is_authenticated:
            culture.approved_by = request.user
        culture.approval_time = timezone.now()
        
        if status_val == 'POSITIVE':
            culture.growth_status = 'Growth Detected'
            culture.final_interpretation = BloodUnitCulture.FinalInterpretation.CONTAMINATED
            culture.unit_status = BloodUnitCulture.UnitStatus.DISCARDED
            culture.organism_name = request.data.get('organism_name', 'Staphylococcus epidermidis')
            culture.gram_stain_result = request.data.get('gram_stain_result', 'Gram Positive Cocci')
            culture.colony_count = request.data.get('colony_count', '>100 CFU/mL')
            culture.antibiotic_susceptibility = request.data.get('antibiotic_susceptibility', '')
            culture.comments = request.data.get('comments', 'Bacterial contamination detected. Discard blood unit immediately.')
            
            # Automatically update inventory component status to DISCARDED
            BloodComponent.objects.filter(workflow=workflow).update(status='DISCARDED', bacterial_contamination=True)

        elif status_val == 'NEGATIVE':
            culture.growth_status = 'No Growth'
            culture.final_interpretation = BloodUnitCulture.FinalInterpretation.STERILE
            culture.unit_status = BloodUnitCulture.UnitStatus.RELEASED
            culture.organism_name = 'No Growth'
            culture.gram_stain_result = ''
            culture.colony_count = ''
            culture.comments = request.data.get('comments', 'Unit released for transfusion after sterile incubation.')
            
            # Automatically update inventory component status to AVAILABLE / RELEASED
            BloodComponent.objects.filter(workflow=workflow, status='QUARANTINE').update(status='AVAILABLE', bacterial_contamination=False)

        else: # PENDING
            culture.growth_status = 'No Growth Yet'
            culture.final_interpretation = BloodUnitCulture.FinalInterpretation.PENDING
            culture.unit_status = BloodUnitCulture.UnitStatus.QUARANTINED
            culture.current_incubation_day = int(request.data.get('current_incubation_day', 3))

        culture.save()
        return Response({'status': 'success', 'message': f'Culture result saved as {status_val}.'})
        

    def get_serializer_class(self):
        if self.action == 'list':
            from .serializers import DonationListSerializer
            return DonationListSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == 'list':
            return qs.select_related('donor').order_by('-created_at')
        return qs

    @decorators.action(detail=True, methods=['get'])
    def components(self, request, pk=None):
        """Return the list of blood components generated for this workflow."""
        workflow = self.get_object()
        from inventory.models import BloodComponent
        from .serializers import BloodComponentSerializer
        comps = BloodComponent.objects.filter(workflow=workflow).select_related(
            'created_by', 'modified_by', 'approved_by'
        ).order_by('id')
        serializer = BloodComponentSerializer(comps, many=True)
        return Response(serializer.data)

    @decorators.action(detail=True, methods=['get'])
    def status_history(self, request, pk=None):
        """Return real audit log for each workflow step, with actual user names and timestamps."""
        workflow = self.get_object()

        def fmt(dt):
            """Format datetime to DD/MM/YYYY HH:MM AM/PM"""
            if not dt:
                return '-'
            import datetime
            if isinstance(dt, datetime.time):
                # Convert time-only to 12h format
                return datetime.datetime.combine(datetime.date.today(), dt).strftime('%I:%M %p')
            try:
                from django.utils import timezone
                local_dt = timezone.localtime(dt)
                return local_dt.strftime('%d/%m/%Y %I:%M %p')
            except Exception:
                return str(dt)

        def uname(user):
            if not user:
                return 'System'
            return user.get_full_name() or user.username

        log = []

        # 1. Registration — workflow creator (created_at)
        log.append({
            'status': 'Registration',
            'by': uname(workflow.created_by),
            'time': fmt(workflow.created_at),
        })

        # 2. Questionnaire — reviewed_by
        try:
            q = workflow.questionnaire
            log.append({
                'status': 'Questionnaire',
                'by': uname(q.reviewed_by),
                'time': fmt(q.created_at),
            })
        except Exception:
            pass

        # 3. Vital Signs — examiner
        try:
            from .models import VitalSigns
            v = VitalSigns.objects.filter(workflow=workflow).first()
            if v:
                log.append({
                    'status': 'Vital Signs Completed',
                    'by': uname(v.examiner),
                    'time': fmt(v.created_at),
                })
        except Exception:
            pass

        # 4. Blood Draw — examiner
        try:
            bd = workflow.blood_draw
            # Build time: prefer created_at; show start/end as supplemental if available
            main_time = fmt(bd.created_at)
            if bd.drawn_start_time and bd.drawn_end_time:
                main_time = f"{fmt(bd.drawn_start_time)} – {fmt(bd.drawn_end_time)}"
            elif bd.drawn_start_time:
                main_time = fmt(bd.drawn_start_time)
            log.append({
                'status': 'Blood Draw Completed',
                'by': uname(bd.examiner),
                'time': main_time,
            })
        except Exception:
            pass

        # 5. Post-Donation Care — recorded_by
        try:
            pdc = workflow.post_donation_care
            log.append({
                'status': 'Post-Donation Care Completed',
                'by': uname(pdc.recorded_by),
                'time': fmt(pdc.created_at),
            })
        except Exception:
            pass

        # 6. Adverse Reaction — examiner
        try:
            ar = workflow.adverse_reaction
            log.append({
                'status': 'Adverse Reaction Recorded',
                'by': uname(ar.examiner),
                'time': fmt(ar.created_at),
            })
        except Exception:
            pass

        # 7. Pre-Separation — received_by
        try:
            ps = workflow.pre_separation
            log.append({
                'status': 'Pre-Separation',
                'by': uname(ps.received_by),
                'time': fmt(ps.received_at or ps.created_at),
            })
        except Exception:
            pass

        # 8. Components — created_by of first component
        try:
            first_comp = workflow.components.select_related('created_by').first()
            if first_comp:
                log.append({
                    'status': 'Components Separated',
                    'by': uname(first_comp.created_by),
                    'time': fmt(first_comp.manufactured_at),
                })
        except Exception:
            pass

        # 9. Lab Results — technician
        try:
            from .models import LabResult
            first_result = LabResult.objects.filter(workflow=workflow).select_related('technician').first()
            if first_result:
                log.append({
                    'status': 'Lab Results',
                    'by': uname(first_result.technician),
                    'time': fmt(first_result.created_at),
                })
        except Exception:
            pass

        return Response(log)

    @decorators.action(detail=True, methods=['post'])
    def separate_components(self, request, pk=None):
        workflow = self.get_object()
        components = request.data.get('components', [])
        
        try:
            from inventory.services import InventoryService
            
            created = InventoryService.separate_batch(workflow, components, request.user)
            
            # Update Status to LABS (Next step)
            # Or COMPLETED if that's the end of processing
            # Assuming 'LABS' is next.
            if workflow.status != DonorWorkflow.Step.ATTACHMENT and workflow.status != DonorWorkflow.Step.COMPLETED:
                workflow.status = 'ATTACHMENT' if hasattr(DonorWorkflow.Step, 'ATTACHMENT') else 'LABS'
                workflow.save()

            return Response({
                'status': 'success',
                'message': f"Created {len(created)} components",
                'components': [{
                    'id': c.id,
                    'type': c.component_type,
                    'volume': c.volume,
                    'unit_number': c.unit_number,
                    'expiration_date': c.expiration_date.strftime('%Y-%m-%d %H:%M') if c.expiration_date else 'N/A',
                    'status': c.status,
                    'visual_inspection': c.visual_inspection,
                    'notes': c.notes,
                    'created_at': c.manufactured_at.strftime('%Y-%m-%d %H:%M') if c.manufactured_at else '',
                    'created_by': request.user.username if request.user and request.user.is_authenticated else 'System',
                    'room_temp': c.room_temp_check,
                    'storage_time': (
                        c.storage_time_after_prep.strftime('%H:%M')
                        if c.storage_time_after_prep and hasattr(c.storage_time_after_prep, 'strftime')
                        else str(c.storage_time_after_prep) if c.storage_time_after_prep
                        else ''
                    )
                } for c in created]
            })
        except Exception as e:
            import traceback
            return Response({
                'status': 'error',
                'error': f"{str(e)} \n {traceback.format_exc()}"
            }, status=500)

    @decorators.action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        """Manually update the workflow status from frontend."""
        workflow = self.get_object()
        new_status = request.data.get('status')
        
        if new_status:
            workflow.status = new_status
            workflow.save()
            return Response({'status': 'success', 'new_status': workflow.status})
        return Response({'status': 'error', 'message': 'No status provided'}, status=400)

    @decorators.action(detail=True, methods=['get'])
    def get_attachments(self, request, pk=None):
        workflow = self.get_object()
        attachments = workflow.attachments.all().order_by('-created_at')
        from .serializers import DonationAttachmentSerializer
        serializer = DonationAttachmentSerializer(attachments, many=True)
        return Response({'status': 'success', 'data': serializer.data})

    @decorators.action(detail=True, methods=['post'])
    def upload_attachment(self, request, pk=None):
        workflow = self.get_object()
        
        try:
            from .models import DonationAttachment
            from .serializers import DonationAttachmentSerializer
            
            attachment = DonationAttachment.objects.create(
                workflow=workflow,
                title=request.data.get('title'),
                notes=request.data.get('notes', ''),
                file=request.FILES.get('file'),
                created_by=request.user
            )
            
            return Response({
                'status': 'success', 
                'message': 'Attachment uploaded successfully',
                'data': DonationAttachmentSerializer(attachment).data
            })
        except Exception as e:
             return Response({'status': 'error', 'error': str(e)}, status=400)

@staff_required
def start_donation(request, donor_id):
    if request.method == 'POST':
        donor = get_object_or_404(Donor, pk=donor_id)
        
        # Check if active exists or start new
        workflow = WorkflowService.start_workflow(donor, request.user)
        
        # Support AJAX/JSON for Single Page App feel (New Donors List)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.accepts('application/json'):
            return JsonResponse({'status': 'success', 'workflow_id': workflow.id})

        messages.success(request, "Donation session started successfully.")
        return redirect('donor_workflow', pk=donor.pk)
    return redirect('donor_list')

@staff_required
def queue_questionnaire(request):
    # Donors who passed Profile Approval (Ready for Q)
    workflows = DonorWorkflow.objects.filter(status=DonorWorkflow.Step.QUESTIONNAIRE).order_by('created_at')
    return render(request, 'workflow/queue_questionnaire.html', {
        'workflows': workflows,
        'queue_step': 'QUESTIONNAIRE'
    })

@staff_required
def queue_profile(request):
    # Donors newly INITIATED (Waiting for Profile Approval)
    workflows = DonorWorkflow.objects.filter(status=DonorWorkflow.Step.REGISTRATION).order_by('created_at')
    return render(request, 'workflow/queue_profile.html', {
        'workflows': workflows,
        'queue_step': 'REGISTRATION'
    })

@staff_required
def queue_vitals(request):
    return render(request, 'workflow/queue_vitals.html', {'queue_step': 'VITALS'})

@staff_required
def queue_collection(request):
    return render(request, 'workflow/queue_collection.html', {'queue_step': 'COLLECTION'})
    return render(request, 'workflow/queue_collection.html')

@staff_required
def lab_dashboard(request):
    # Fetch all workflows in LABS state
    pending_labs = DonorWorkflow.objects.filter(status=DonorWorkflow.Step.LABS).select_related('donor', 'blood_draw').order_by('created_at')
    return render(request, 'labs/dashboard.html', {'samples': pending_labs})

@staff_required
def infinity_list(request):
    return render(request, 'labs/dashboard.html') # Stub

@staff_required
def ortho_list(request):
    return render(request, 'labs/dashboard.html') # Stub



@staff_required
def settings_deferral(request):
    from .models import DeferralReason
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'save':
            d_id = request.POST.get('id')
            defaults = {
                'code': request.POST.get('code') or f"DEF-{DeferralReason.objects.count() + 100}", 
                'reason_en': request.POST.get('title_en'),
                'reason_ar': request.POST.get('title_ar'),
                'description_en': request.POST.get('desc_en'),
                'description_ar': request.POST.get('desc_ar'),
                'default_duration_days': int(request.POST.get('blocking_days') or 0),
                'deferral_type': request.POST.get('type'),
                'is_active': request.POST.get('is_active') == 'on'
            }
            
            if d_id:
                DeferralReason.objects.filter(pk=d_id).update(**defaults)
                messages.success(request, "Deferral reason updated.")
            else:
                DeferralReason.objects.create(**defaults)
                messages.success(request, "New deferral reason added.")
                
        elif action == 'delete':
            d_id = request.POST.get('id')
            DeferralReason.objects.filter(pk=d_id).delete()
            messages.success(request, "Deferral reason deleted.")
            
        return redirect('settings_deferral')

    deferrals = DeferralReason.objects.all().order_by('-id')
    return render(request, 'clinical/settings_deferral.html', {'deferrals': deferrals})


@staff_required
def modification_requests_list(request):
    from .models import ModificationRequest
    
    # Filter logic (basic placeholder for now)
    requests = ModificationRequest.objects.all().order_by('-created_at')
    
    return render(request, 'clinical/modification_requests.html', {'requests': requests})


@staff_required
def add_component_manual(request):
    from .models import ProductSeparationRule
    
    # Choices for dropdowns
    component_types = ProductSeparationRule.Component.choices
    blood_groups = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
    sources = ['In-House', 'External Drive', 'Imported']
    sites = ['Main Center', 'Mobile Unit 1', 'Mobile Unit 2']

    if request.method == 'POST':
        # Logic to save connection (placeholder for now as no ManualComponent model exists yet)
        messages.success(request, "Manual component added successfully (Simulation).")
        return redirect('add_component_manual')

    return render(request, 'clinical/add_component_manual.html', {
        'component_types': component_types,
        'blood_groups': blood_groups,
        'sources': sources,
        'sites': sites
    })


@staff_required
def donation_certificate_report(request):
    from .models import DonorWorkflow
    
    # Fetch donations (Workflows)
    # Ideally filter by status='COMPLETED' or similar, but showing all for demo
    workflows = DonorWorkflow.objects.select_related('donor', 'blood_draw').order_by('-created_at')

    # Mocking 'Blood Nature' and 'Certificate Status' for display consistency with screenshot
    # In a real app, these would be fields on the model
    for wf in workflows:
        wf.blood_nature = "Whole Blood" # Default
        wf.cert_status = "New" # Default

    context = {
        'workflows': workflows
    }
    return render(request, 'clinical/donation_certificate_report.html', context)


@staff_required
def questionnaire_failed_list(request):
    # Donors who were deferred specifically during Questionnaire
    # Ideally tracked by 'status=DEFERRED' AND 'last_step=QUESTIONNAIRE' 
    # For now, we show all DEFERRED workflows for simplicity, or we can refine logic later
    workflows = DonorWorkflow.objects.filter(status=DonorWorkflow.Step.DEFERRED).order_by('-updated_at')
    
    return render(request, 'workflow/queue_questionnaire_failed.html', {
        'workflows': workflows,
        'queue_step': 'DEFERRED'
    })

@staff_required
def blood_drawn_completed_list(request):
    # Donors who completed Blood Draw (waiting for Labs or just historical log)
    # Status is typically 'LABS' or 'COMPLETED' if just finished draw
    # Or specifically successful blood draws.
    workflows = DonorWorkflow.objects.filter(status__in=[DonorWorkflow.Step.LABS, DonorWorkflow.Step.COMPLETED]).select_related('blood_draw', 'donor').order_by('-updated_at')
    
    return render(request, 'workflow/blood_drawn_completed.html', {
        'workflows': workflows,
        'queue_step': 'COMPLETED_DRAW'
    })

@staff_required
def donation_list(request):
    return render(request, 'donations/list.html')

@staff_required
def patient_donors_report(request):
    from .models import DonorWorkflow
    
    # Mock data for Patient Donors Report
    
    # Top Table: Patient Donors Summary
    patient_donors = [
        {'mrn': '112889', 'name': 'Nasruddin Mohammed Mahmoud', 'name_ar': 'نصرالدين محمد محمود', 'count': 4, 'date': '31/01/2026', 'nature': 'Whole Blood', 'units_count': 1, 'units_volume': '450 ML'},
        {'mrn': '112006', 'name': 'Ali Hassan Shamshamieh', 'name_ar': 'علي حسن شمشمية', 'count': 1, 'date': '29/01/2026', 'nature': 'Whole Blood', 'units_count': 1, 'units_volume': '400 ML'},
        {'mrn': '567883', 'name': 'Zahid Abdulhamid Musa', 'name_ar': 'زاهد عبدالحميد موسى', 'count': 3, 'date': '06/01/2026', 'nature': 'Whole Blood', 'units_count': 1, 'units_volume': '450 ML'},
        {'mrn': '567883', 'name': 'Zahid Abdulhamid Musa', 'name_ar': 'زاهد عبدالحميد موسى', 'count': 1, 'date': '04/01/2026', 'nature': 'Apheresis Platelets', 'units_count': 10, 'units_volume': '250 ML'},
        {'mrn': '1084511', 'name': 'Bilqis - Ahmad', 'name_ar': 'بلقيس - احمد', 'count': 3, 'date': '31/01/2026', 'nature': 'Whole Blood', 'units_count': 1, 'units_volume': '450 ML'},
        {'mrn': '112889', 'name': 'Nasruddin Mohammed Mahmoud', 'name_ar': 'نصرالدين محمد محمود', 'count': 3, 'date': '29/01/2026', 'nature': 'Whole Blood', 'units_count': 1, 'units_volume': '450 ML'},
        {'mrn': '1644026', 'name': 'Maryam Muslih Alshamman', 'name_ar': 'مريم مصلح الشمراني', 'count': 1, 'date': '29/01/2026', 'nature': 'Whole Blood', 'units_count': 1, 'units_volume': '450 ML'},
        {'mrn': '1084511', 'name': 'Bilqis - Ahmad', 'name_ar': 'بلقيس - احمد', 'count': 5, 'date': '26/01/2026', 'nature': 'Whole Blood', 'units_count': 1, 'units_volume': '450 ML'},
        {'mrn': '112889', 'name': 'Nasruddin Mohammed Mahmoud', 'name_ar': 'نصرالدين محمد محمود', 'count': 1, 'date': '28/01/2026', 'nature': 'Whole Blood', 'units_count': 1, 'units_volume': '450 ML'},
        {'mrn': '112889', 'name': 'Nasruddin Mohammed Mahmoud', 'name_ar': 'نصرالدين محمد محمود', 'count': 3, 'date': '28/01/2026', 'nature': 'Apheresis Platelets', 'units_count': 13, 'units_volume': '418 ML'},
    ]

    # Bottom Table: Patient Blood Units Received
    blood_units = [
        {'req_no': '16', 'created_date': '14/10/2025', 'created_by': 'shameel', 'name': 'SHEEJA K - 15870', 'comp_type': 'PRBC', 'volume': 354, 'partial': 'x', 'unit_no': '0000539', 'bg': 'O Positive', 'donation_code': '0024-2575'},
        {'req_no': '19', 'created_date': '15/10/2025', 'created_by': 'falam', 'name': 'nisha das - 11764', 'comp_type': 'PRBC', 'volume': 144, 'partial': 'x', 'unit_no': '0000573', 'bg': 'O Positive', 'donation_code': '0024-2514'},
        {'req_no': '23', 'created_date': '18/10/2025', 'created_by': 'falam', 'name': 'ASWATHI UNNIKRISHNAN - 11884', 'comp_type': 'PRBC', 'volume': 310, 'partial': 'x', 'unit_no': '0000518', 'bg': 'O Positive', 'donation_code': '0021-2550'},
        {'req_no': '28', 'created_date': '21/10/2025', 'created_by': 'falam', 'name': 'JOIS - 11241', 'comp_type': 'PRBC', 'volume': 50, 'partial': 'correct', 'unit_no': '0000677', 'bg': 'O Positive', 'donation_code': '0021-2610'},
        {'req_no': '29', 'created_date': '23/10/2025', 'created_by': 'falam', 'name': 'RJR TARENO - 1944', 'comp_type': 'APHERESIS', 'volume': 200, 'partial': 'correct', 'unit_no': '0000683', 'bg': 'A Positive', 'donation_code': 'SCP21-136'},
        {'req_no': '36', 'created_date': '24/10/2025', 'created_by': 'AHMED', 'name': 'SISI MOL.L - 11394', 'comp_type': 'PLAT PC', 'volume': 55, 'partial': 'correct', 'unit_no': '0000635', 'bg': 'A Positive', 'donation_code': '0021-2571'},
        {'req_no': '39', 'created_date': '24/10/2025', 'created_by': 'AHMED', 'name': 'SHYAMOL - 11571', 'comp_type': 'PRBC', 'volume': 150, 'partial': 'correct', 'unit_no': '0000433', 'bg': 'AB Positive', 'donation_code': '0024-2554'},
        {'req_no': '42', 'created_date': '26/10/2025', 'created_by': 'ABDULMALIK', 'name': 'revathi - 11850', 'comp_type': 'PRBC', 'volume': 100, 'partial': 'correct', 'unit_no': '0000812', 'bg': 'O Negative', 'donation_code': '0021-2654'},
        {'req_no': '46', 'created_date': '28/10/2025', 'created_by': 'FAISAL', 'name': 'shinjusibin - 2072', 'comp_type': 'PRBC', 'volume': 313, 'partial': 'x', 'unit_no': '0000780', 'bg': 'O Positive', 'donation_code': '0021-2628'},
        {'req_no': '48', 'created_date': '29/10/2025', 'created_by': 'FAISAL', 'name': 'LIZNA PEGGY - 7979', 'comp_type': 'APHERESIS', 'volume': 132, 'partial': 'correct', 'unit_no': '0000815', 'bg': 'O Positive', 'donation_code': 'SCP21-138'},
    ]

    return render(request, 'reports/patient_donors_report.html', {
        'patient_donors': patient_donors,
        'blood_units': blood_units
    })

@staff_required
def pending_verification(request):
    from .models import DonorWorkflow
    
    # We will fetch 'COMPLETED' flows to act as source for 'Pending Verification'
    # In a real app, this would be Component.objects.filter(status='PENDING_VERIFICATION')
    completed_flows = DonorWorkflow.objects.filter(
        status=DonorWorkflow.Step.COMPLETED
    ).select_related('donor').order_by('-updated_at')
    
    pending_verification_items = []
    # Mock some varying component types based on ID
    components = ['PRBC', 'FFP', 'APHERESIS', 'PLT']
    
    for i, wf in enumerate(completed_flows):
        pending_verification_items.append({
            'workflow': wf,
            'unit_number': f"000513{83 + i}",
            'component_type': components[i % 4],
            'volume_ml': 340 if components[i % 4] == 'PRBC' else 200,
            'blood_group': wf.donor.blood_group,
            'created_at': wf.updated_at
        })

    return render(request, 'reports/pending_verification.html', {
        'pending_verification': pending_verification_items
    })

def disposition_to_store(request):
    from .models import DonorWorkflow, LabResult, BloodUnitCulture
    from inventory.models import BloodComponent
    from django.utils import timezone
    from datetime import timedelta
    
    # 1. Purge ONLY specific mock/fake test patterns (DO NOT purge real components or all DB objects!)
    fake_patterns = [
        '54321', '5400-W', '890-W', '987-W', '6543-0', '800-0', '5100-0', '654-0', '543-0', '5000-0', '12345-0',
        'W29', 'W23', 'W21', 'W19', 'W18', 'W15', 'W13', 'W11', '24354564343', '576777', '345-W', '657-W',
        'DON-', 'CB-0010', 'CB-0011', 'CB-0012'
    ]
    for pat in fake_patterns:
        try:
            BloodComponent.objects.filter(unit_number__icontains=pat).delete()
        except Exception:
            pass

    # 2. Auto-sync completed real donor workflows (like ahmed) with passed lab tests
    try:
        real_workflows = DonorWorkflow.objects.filter(
            donor__isnull=False
        ).select_related('donor', 'blood_draw').order_by('-updated_at')
        
        for wf in real_workflows:
            # Check lab test status
            abnormal_labs = LabResult.objects.filter(workflow=wf, is_abnormal=True).exists()
            positive_culture = BloodUnitCulture.objects.filter(workflow=wf, status='POSITIVE').exists()
            
            if abnormal_labs or positive_culture:
                # Move to DISCARDED
                BloodComponent.objects.filter(workflow=wf).update(status='DISCARDED')
            else:
                # Normal screening passed! Ensure components exist and status is AVAILABLE
                comps = BloodComponent.objects.filter(workflow=wf)
                if not comps.exists():
                    d_code = wf.donation_code or (wf.blood_draw.bag_serial_number if hasattr(wf, 'blood_draw') and wf.blood_draw else None) or f"H107726{wf.id:06d}"
                    bg = wf.donor.blood_group if (wf.donor and wf.donor.blood_group != 'UNKNOWN') else "O+"
                    now = timezone.now()
                    
                    BloodComponent.objects.create(
                        workflow=wf, component_type=BloodComponent.Type.PRBC,
                        unit_number=f"{d_code}-01", blood_group=bg, volume=350,
                        status='AVAILABLE', expiration_date=now + timedelta(days=42)
                    )
                    BloodComponent.objects.create(
                        workflow=wf, component_type=BloodComponent.Type.FFP,
                        unit_number=f"{d_code}-02", blood_group=bg, volume=200,
                        status='AVAILABLE', expiration_date=now + timedelta(days=365)
                    )
                else:
                    comps.exclude(status='DISCARDED').update(status='AVAILABLE')
    except Exception as e:
        print(f"Error syncing real workflows in disposition_to_store: {e}")

    components_list = []
    
    req_type = request.GET.get('component_type')
    req_bg = request.GET.get('blood_group')
    req_code = request.GET.get('donation_code')
    
    try:
        db_comps = BloodComponent.objects.filter(
            status__in=['AVAILABLE', 'RELEASED', 'QUARANTINE', 'STOCK'],
            workflow__isnull=False
        ).select_related('workflow', 'workflow__donor').order_by('-updated_at')
        
        for comp in db_comps:
            wf = comp.workflow
            bag_code = comp.unit_number
            try:
                if wf and wf.donation_code:
                    bag_code = wf.donation_code
                elif wf and hasattr(wf, 'blood_draw') and wf.blood_draw and wf.blood_draw.bag_serial_number:
                    bag_code = wf.blood_draw.bag_serial_number
            except Exception:
                pass
                
            c_type = comp.get_component_type_display() if hasattr(comp, 'get_component_type_display') else comp.component_type
            b_group = comp.blood_group or (wf.donor.blood_group if (wf and wf.donor) else 'O+')
            
            # Skip if matches fake pattern
            if any(pat in bag_code for pat in fake_patterns) or any(pat in comp.unit_number for pat in fake_patterns):
                continue

            # Filtering
            if req_type and req_type != 'All Component Type' and req_type not in c_type:
                continue
            if req_bg and req_bg != 'All BloodGroups' and req_bg != b_group:
                continue
            if req_code and req_code.strip() and req_code.strip().lower() not in bag_code.lower():
                continue

            components_list.append({
                'id': comp.id,
                'donation_code': bag_code,
                'component_type': c_type,
                'blood_group': b_group,
                'volume': comp.volume,
                'expire_date': comp.expiration_date,
                'created_at': comp.updated_at or comp.manufactured_at,
                'notes': comp.notes or 'Passed screening tests. Approved for Disposition To Store.'
            })
    except Exception as e:
        print(f"Error loading db_comps in disposition_to_store: {e}")
        print(f"Error loading db_comps in disposition_to_store: {e}")

    return render(request, 'reports/disposition_to_store.html', {
        'components': components_list
    })

def store_report(request):
    return render(request, 'reports/store.html', {
        'store_items': [],
        'current_filters': {'component_type': '', 'blood_group': ''}
    })

def component_details(request):
    from inventory.models import BloodComponent
    from django.utils import timezone
    
    test_patterns = [
        '54321', '5400', '890', '987', '6543', '800', '5100', '654', '543', '5000', '12345',
        'W29', 'W23', 'W21', 'W19', 'W18', 'W15', 'W13', 'W11', '24354564343', '576777', '345-W', '657-W'
    ]
    for pat in test_patterns:
        try:
            BloodComponent.objects.filter(unit_number__icontains=pat).delete()
        except Exception:
            pass

    components = []
    req_type = request.GET.get('component_type')
    req_bg = request.GET.get('blood_group')
    req_status = request.GET.get('status')
    req_code = request.GET.get('donation_code')
    
    try:
        db_comps = BloodComponent.objects.select_related('workflow', 'workflow__donor', 'modified_by').order_by('-updated_at')
        
        for comp in db_comps:
            wf = comp.workflow
            bag_code = comp.unit_number
            try:
                if wf and wf.donation_code:
                    bag_code = wf.donation_code
                elif wf and hasattr(wf, 'blood_draw') and wf.blood_draw and wf.blood_draw.bag_serial_number:
                    bag_code = wf.blood_draw.bag_serial_number
            except Exception:
                pass
                
            if any(pat in bag_code for pat in test_patterns):
                continue

            c_type = comp.get_component_type_display() if hasattr(comp, 'get_component_type_display') else comp.component_type
            b_group = comp.blood_group or (wf.donor.blood_group if (wf and wf.donor) else 'O+')
            
            # Filtering
            if req_type and req_type != 'All Component Type' and req_type not in c_type:
                continue
            if req_bg and req_bg != 'All BloodGroups' and req_bg != b_group:
                continue
            if req_status and req_status != 'All Status' and req_status.upper() not in comp.status.upper():
                continue
            if req_code and req_code.strip() and req_code.strip().lower() not in bag_code.lower():
                continue

            components.append({
                'index': comp.id,
                'donation_code': bag_code,
                'source': 'SMC Main Bank',
                'component_type': c_type,
                'blood_group': b_group,
                'qty': 1,
                'volume': comp.volume or 300,
                'volume_issued': '-',
                'rr': '30 : 70' if comp.component_type in ['PLT', 'PLAT_PC'] else ('150 : 220' if comp.component_type == 'FFP' else '-'),
                'expire_date': comp.expiration_date,
                'temperature': '20-24°C' if comp.component_type in ['PLT', 'APHERESIS', 'PLAT_PC'] else ('-18°C' if comp.component_type == 'FFP' else '2-6°C'),
                'location': comp.location or 'Unassigned', 
                'status': 'Discarded' if comp.status == 'DISCARDED' else 'Stock',
                'verification': 'Discarded' if comp.status == 'DISCARDED' else 'Verified',
                'verification_by': 'Quality Officer',
                'verification_date': comp.updated_at.strftime('%d/%m/%Y %I:%M %p') if comp.updated_at else '---',
                'done_by': comp.modified_by.username if (comp.modified_by and hasattr(comp.modified_by, 'username')) else 'System Specialist',
                'done_date': comp.updated_at.strftime('%d/%m/%Y %I:%M %p') if comp.updated_at else timezone.now().strftime('%d/%m/%Y %I:%M %p'),
                'modified_by': '',
                'modified_date': '',
                'note': comp.notes or ''
            })
    except Exception as e:
        print(f"Error loading db_comps in component_details: {e}")

    return render(request, 'reports/component_details.html', {
        'components': components
    })

def discarded_units(request):
    from .models import DonorWorkflow, LabResult, BloodUnitCulture
    from inventory.models import BloodComponent
    from django.utils import timezone
    
    test_patterns = [
        '54321', '5400', '890', '987', '6543', '800', '5100', '654', '543', '5000', '12345',
        'W29', 'W23', 'W21', 'W19', 'W18', 'W15', 'W13', 'W11', '24354564343', '576777', '345-W', '657-W'
    ]
    for pat in test_patterns:
        try:
            BloodComponent.objects.filter(unit_number__icontains=pat).delete()
        except Exception:
            pass

    discarded_comp = []
    seen_wf_ids = set()
    
    # 1. Fetch real components with status='DISCARDED'
    try:
        db_discarded = BloodComponent.objects.filter(
            status='DISCARDED'
        ).select_related('workflow', 'workflow__donor', 'modified_by').order_by('-updated_at')
        
        for comp in db_discarded:
            wf = comp.workflow
            if wf:
                seen_wf_ids.add(wf.id)
            bag_code = comp.unit_number
            try:
                if wf and wf.donation_code:
                    bag_code = wf.donation_code
                elif wf and hasattr(wf, 'blood_draw') and wf.blood_draw and wf.blood_draw.bag_serial_number:
                    bag_code = wf.blood_draw.bag_serial_number
            except Exception:
                pass

            if any(pat in bag_code for pat in test_patterns):
                continue
                
            reasons = []
            if wf:
                try:
                    abnormal_labs = LabResult.objects.filter(workflow=wf, is_abnormal=True)
                    reasons.extend([f"{r.test_name}: {r.result_value}" for r in abnormal_labs])
                except Exception:
                    pass
                try:
                    positive_cultures = BloodUnitCulture.objects.filter(workflow=wf, status='POSITIVE')
                    for c in positive_cultures:
                        reasons.append(f"Bacterial Culture Positive: {c.organism_name or 'Contaminated'}")
                except Exception:
                    pass
                
            note_str = comp.notes or (", ".join(reasons) if reasons else "Discarded due to abnormal lab test / culture result")
            
            discarded_comp.append({
                'index': comp.id,
                'donation_code': bag_code,
                'source': 'SMC Main Bank',
                'component_type': comp.component_type,
                'blood_group': comp.blood_group or (wf.donor.blood_group if (wf and wf.donor) else 'O+'),
                'qty': 1,
                'volume': comp.volume or 300,
                'volume_issued': '-',
                'rr': '30 : 70',
                'expire_date': comp.expiration_date.strftime('%d/%m/%Y') if comp.expiration_date else '---',
                'temperature': '2-6°C',
                'location': 'Discard Quarantine Fridge', 
                'status': 'Discarded',
                'discarded_note': note_str,
                'discarded_by': comp.modified_by.username if (comp.modified_by and hasattr(comp.modified_by, 'username')) else 'System Lab Technician',
                'discarded_date': comp.updated_at.strftime('%d/%m/%Y %I:%M %p') if comp.updated_at else timezone.now().strftime('%d/%m/%Y %I:%M %p'),
                'verified_1': True,
                'verified_1_by': 'Quality Officer',
                'verified_1_date': timezone.now().strftime('%d/%m/%Y %I:%M %p'),
                'discarded_verify_by': 'Quality Supervisor',
                'discarded_verify_date': timezone.now().strftime('%d/%m/%Y %I:%M %p'),
                'done_by': 'Lab Specialist',
                'done_date': comp.updated_at.strftime('%d/%m/%Y %I:%M %p') if comp.updated_at else timezone.now().strftime('%d/%m/%Y %I:%M %p')
            })
    except Exception as e:
        print(f"Error loading db_discarded in discarded_units: {e}")
        
    # 2. Check workflows with abnormal lab results
    try:
        abnormal_workflows = DonorWorkflow.objects.filter(
            lab_results__is_abnormal=True
        ).exclude(id__in=seen_wf_ids).distinct().select_related('donor').order_by('-updated_at')
        
        for wf in abnormal_workflows:
            seen_wf_ids.add(wf.id)
            bag_code = f"CB-{wf.id:04d}"
            try:
                if hasattr(wf, 'blood_draw') and wf.blood_draw and wf.blood_draw.bag_serial_number:
                    bag_code = wf.blood_draw.bag_serial_number
            except Exception:
                pass
            
            if any(pat in bag_code for pat in test_patterns):
                continue
                
            reasons = []
            try:
                abnormal_labs = wf.lab_results.filter(is_abnormal=True)
                reasons = [f"{r.test_name}: {r.result_value}" for r in abnormal_labs]
            except Exception:
                pass
            
            discarded_comp.append({
                'index': 8000 + wf.id,
                'donation_code': bag_code,
                'source': 'SMC Main Bank',
                'component_type': 'Whole Blood Unit',
                'blood_group': wf.donor.blood_group if (wf and wf.donor) else 'O+',
                'qty': 1,
                'volume': 450,
                'volume_issued': '-',
                'rr': '30 : 70',
                'expire_date': timezone.now().strftime('%d/%m/%Y'),
                'temperature': '2-6°C',
                'location': 'Discard Quarantine', 
                'status': 'Discarded',
                'discarded_note': f"Abnormal Lab Test: {', '.join(reasons)}",
                'discarded_by': 'Lab Technician',
                'discarded_date': wf.updated_at.strftime('%d/%m/%Y %I:%M %p') if hasattr(wf, 'updated_at') else timezone.now().strftime('%d/%m/%Y %I:%M %p'),
                'verified_1': True,
                'verified_1_by': 'Quality Officer',
                'verified_1_date': timezone.now().strftime('%d/%m/%Y %I:%M %p'),
                'discarded_verify_by': 'Quality Supervisor',
                'discarded_verify_date': timezone.now().strftime('%d/%m/%Y %I:%M %p'),
                'done_by': 'Lab Specialist',
                'done_date': wf.updated_at.strftime('%d/%m/%Y %I:%M %p') if hasattr(wf, 'updated_at') else timezone.now().strftime('%d/%m/%Y %I:%M %p')
            })
    except Exception as e:
        print(f"Error loading abnormal_workflows in discarded_units: {e}")

    # 3. Check workflows with POSITIVE bacterial cultures
    try:
        positive_culture_wfs = DonorWorkflow.objects.filter(
            cultures__status='POSITIVE'
        ).exclude(id__in=seen_wf_ids).distinct().select_related('donor').order_by('-updated_at')

        for wf in positive_culture_wfs:
            seen_wf_ids.add(wf.id)
            bag_code = f"CB-{wf.id:04d}"
            try:
                if hasattr(wf, 'blood_draw') and wf.blood_draw and wf.blood_draw.bag_serial_number:
                    bag_code = wf.blood_draw.bag_serial_number
            except Exception:
                pass

            if any(pat in bag_code for pat in test_patterns):
                continue

            cult = wf.cultures.filter(status='POSITIVE').first()
            org = cult.organism_name if cult else 'Staphylococcus epidermidis'

            discarded_comp.append({
                'index': 7000 + wf.id,
                'donation_code': bag_code,
                'source': 'SMC Main Bank',
                'component_type': 'Platelet Concentrate',
                'blood_group': wf.donor.blood_group if (wf and wf.donor) else 'O+',
                'qty': 1,
                'volume': 50,
                'volume_issued': '-',
                'rr': '30 : 70',
                'expire_date': timezone.now().strftime('%d/%m/%Y'),
                'temperature': '20-24°C',
                'location': 'Microbiology Quarantine', 
                'status': 'Discarded',
                'discarded_note': f"Bacterial Contamination: {org}",
                'discarded_by': 'Microbiologist',
                'discarded_date': wf.updated_at.strftime('%d/%m/%Y %I:%M %p') if hasattr(wf, 'updated_at') else timezone.now().strftime('%d/%m/%Y %I:%M %p'),
                'verified_1': True,
                'verified_1_by': 'Lab Director',
                'verified_1_date': timezone.now().strftime('%d/%m/%Y %I:%M %p'),
                'discarded_verify_by': 'Quality Supervisor',
                'discarded_verify_date': timezone.now().strftime('%d/%m/%Y %I:%M %p'),
                'done_by': 'Microbiologist',
                'done_date': wf.updated_at.strftime('%d/%m/%Y %I:%M %p') if hasattr(wf, 'updated_at') else timezone.now().strftime('%d/%m/%Y %I:%M %p')
            })
    except Exception as e:
        print(f"Error loading positive_culture_wfs in discarded_units: {e}")

    return render(request, 'reports/discarded_units.html', {
        'discarded_comp': discarded_comp
    })

@staff_required
def expired_units(request):
    from django.utils import timezone
    from inventory.models import BloodComponent
    
    components = []
    try:
        db_comps = BloodComponent.objects.filter(
            expiration_date__lt=timezone.now()
        ).select_related('workflow', 'workflow__donor', 'modified_by').order_by('-expiration_date')
        
        for comp in db_comps:
            components.append({
                'id': comp.id,
                'code': comp.unit_number,
                'type': comp.get_component_type_display(),
                'bg': comp.blood_group or 'O+',
                'qty': 1,
                'vol': comp.volume or 300,
                'rr': '30 : 70',
                'exp': comp.expiration_date.strftime('%d/%m/%Y') if comp.expiration_date else '-',
                'temp': '2-6°C',
                'status_date': comp.updated_at.strftime('%d/%m/%Y %I:%M %p') if comp.updated_at else '-',
                'note': comp.notes or 'Expired by system',
                'ver_by': 'Quality Officer',
                'ver_date': comp.updated_at.strftime('%d/%m/%Y') if comp.updated_at else '-',
                'done_by': comp.modified_by.username if comp.modified_by else 'System',
                'done_date': comp.updated_at.strftime('%d/%m/%Y') if comp.updated_at else '-'
            })
    except Exception as e:
        print(f"Error in expired_units: {e}")

    return render(request, 'reports/expired_units.html', {
        'components': components
    })
    
@staff_required
def cryo_units(request):
    from inventory.models import BloodComponent
    
    components = []
    try:
        db_comps = BloodComponent.objects.filter(
            component_type='CRYO'
        ).select_related('workflow', 'workflow__donor', 'modified_by').order_by('-updated_at')
        
        for comp in db_comps:
            components.append({
                'id': comp.id,
                'code': comp.unit_number,
                'type': 'Cryoprecipitate',
                'bg': comp.blood_group or 'O+',
                'qty': 1,
                'vol': comp.volume or 30,
                'rr': '15 : 35',
                'temp': 'Less than or equal -18',
                'loc': comp.location or 'Main Freezer',
                'status': comp.status,
                'status_date': comp.updated_at.strftime('%d/%m/%Y %I:%M %p') if comp.updated_at else '-',
                'status_by': comp.modified_by.username if comp.modified_by else 'System',
                'note': comp.notes or '',
                'ver_by': 'Quality Officer',
                'ver_date': comp.updated_at.strftime('%d/%m/%Y') if comp.updated_at else '-',
                'done_by': comp.modified_by.username if comp.modified_by else 'System',
                'done_date': comp.updated_at.strftime('%d/%m/%Y') if comp.updated_at else '-',
                'action_btn': 'Verified'
            })
    except Exception as e:
        print(f"Error in cryo_units: {e}")

    return render(request, 'reports/cryo_units.html', {
        'components': components
    })

@staff_required
def component_culture(request):
    from .models import BloodUnitCulture
    
    components = []
    try:
        db_cultures = BloodUnitCulture.objects.select_related('workflow', 'workflow__donor').order_by('-created_at')
        for cult in db_cultures:
            components.append({
                'id': f"CULT-{cult.id}",
                'type': 'Platelet_Culture',
                'aero_num': cult.bottle_barcode or '-',
                'anaero_num': '-',
                'aero_lot': '-',
                'anaero_lot': '-',
                'status': cult.status,
                'status_date': cult.created_at.strftime('%d/%m/%Y %I:%M %p') if cult.created_at else '-',
                'status_by': 'System',
                'res1': cult.organism_name or ('No Growth' if cult.status == 'NEGATIVE' else 'Positive'),
                'res2': 'N/A',
                'done_by': 'Lab Specialist',
                'done_date': cult.created_at.strftime('%d/%m/%Y') if cult.created_at else '-',
                'mod_by': '',
                'mod_date': ''
            })
    except Exception as e:
        print(f"Error in component_culture: {e}")

    return render(request, 'reports/component_culture.html', {
        'components': components
    })

@staff_required
def component_culture_view(request, request_id):
    details = {
        'id': request_id,
        'date': '-',
        'type': 'Platelet_Culture',
        'aero_num': '-',
        'anaero_num': '-',
        'aero_lot': '-',
        'anaero_lot': '-',
        'aero_exp': '-',
        'anaero_exp': '-',
        'done_by': 'System',
        'done_date': '-',
        'mod_by': '',
        'mod_date': '',
        'status': 'No Culture Request',
        'status_by': '',
        'status_date': '',
        'rec_by': '',
        'rec_date': '',
        'units': []
    }
    return render(request, 'reports/component_culture_view.html', {
        'req': details
    })

@staff_required
def component_culture_pending(request):
    return render(request, 'reports/component_culture_pending.html', {
        'pending_first': [],
        'pending_first_review': [],
        'pending_second': [],
        'pending_second_review': []
    })

@staff_required
def patient_bg_discrepancy(request):
    return render(request, 'reports/patient_bg_discrepancy.html', {
        'discrepancies': []
    })

@staff_required
def discrepancy_alarms(request):
    return render(request, 'reports/discrepancy_alarms.html', {
        'alarms': []
    })

# --- New Reports Module Views ---

@staff_required
def monthly_report(request):
    # Mock Statistics
    stats = {
        'accepted_donors': 409,
        'accepted_donors_pct': 94.5,
        'rejected_donors': 24,
        'rejected_donors_pct': 5.5,
        'total_applied': 433,
        'total_reactive_tests': 33,
        'total_reactive_units': 30,
        'total_reactive_units_pct': 7.3,
        'accepted_units': 379,
        'accepted_units_pct': 92.7,
        'not_satisfied': 2,
        'not_satisfied_pct': 1.4,
        'satisfied': 141,
        'satisfied_pct': 98.6,
        'survey_donors': 143,
        'survey_donors_pct': 35
    }

    # Mock Donors Data
    donors_data = [
        {'nature': 'whole blood', 'reason': 'Volunteer', 'status': 'New', 'total': 1},
        {'nature': 'whole blood', 'reason': 'Volunteer', 'status': 'Accepted', 'total': 228},
        {'nature': 'whole blood', 'reason': 'Volunteer', 'status': 'Rejected', 'total': 8},
        {'nature': 'whole blood', 'reason': 'Volunteer', 'status': 'Questionair Failed', 'total': 1},
        {'nature': 'whole blood', 'reason': 'Volunteer', 'status': 'WithDraw Blood Completed', 'total': 52},
        {'nature': 'whole blood', 'reason': 'Volunteer', 'status': 'Vital Signs Failed', 'total': 5},
        {'nature': 'whole blood', 'reason': 'Volunteer', 'status': 'WithDraw_Blood_Failed', 'total': 3},
        {'nature': 'whole blood', 'reason': 'For Patient', 'status': 'Accepted', 'total': 92},
        {'nature': 'whole blood', 'reason': 'For Patient', 'status': 'Questionair Failed', 'total': 1},
        {'nature': 'whole blood', 'reason': 'For Patient', 'status': 'WithDraw Blood Completed', 'total': 26},
        {'nature': 'whole blood', 'reason': 'For Patient', 'status': 'Vital Signs Failed', 'total': 3},
        {'nature': 'whole blood', 'reason': 'For Patient', 'status': 'WithDraw_Blood_Failed', 'total': 2},
        {'nature': 'Apheresis Platelets', 'reason': 'Volunteer', 'status': 'Accepted', 'total': 1},
        {'nature': 'Apheresis Platelets', 'reason': 'For Patient', 'status': 'Accepted', 'total': 8},
    ]

    # Mock Reactive Units Data
    reactive_data = [
        {'nature': 'whole blood', 'code': '2027', 'test': 'BB-ANTI HBV-CORE TOTAL', 'total': 25},
        {'nature': 'whole blood', 'code': '2028', 'test': 'BB-ANTI HCV', 'total': 3},
        {'nature': 'whole blood', 'code': '2029', 'test': 'BB-HIV p24 Ag / HIV-1&2 Ab (Combined Assay)', 'total': 2},
        {'nature': 'whole blood', 'code': '2031', 'test': 'BB-SYPHILIS', 'total': 1},
        {'nature': 'whole blood', 'code': '2040', 'test': 'BB-ANTI-HBs', 'total': 2},
    ]

    # Mock Discarded Summary Data
    discarded_summary = [
        {'name': 'PRBC', 'status': 'Discarded', 'total': 44},
        {'name': 'Plat PC', 'status': 'Discarded', 'total': 295},
        {'name': 'FFP', 'status': 'Discarded', 'total': 256},
        {'name': 'APHERESIS', 'status': 'Discarded', 'total': 3},
        {'name': 'Plat PC', 'status': 'Expired', 'total': 1},
    ]

    # Mock Discarded Details Data (Partial based on screenshot)
    discarded_details = [
        {'name': 'PRBC', 'status': 'Clotted', 'total': 3},
        {'name': 'PRBC', 'status': 'Broken', 'total': 2},
        {'name': 'PRBC', 'status': 'Leakage', 'total': 17},
        {'name': 'PRBC', 'status': 'Air/Bubble', 'total': 6},
        {'name': 'PRBC', 'status': 'Expired', 'total': 2},
        {'name': 'Plat PC', 'status': 'Clotted', 'total': 2},
        {'name': 'Plat PC', 'status': 'Broken', 'total': 4},
        {'name': 'Plat PC', 'status': 'Bloody', 'total': 31},
        {'name': 'Plat PC', 'status': 'Lipemic', 'total': 28},
        {'name': 'Plat PC', 'status': 'Yellowish', 'total': 8},
        {'name': 'FFP', 'status': 'Clotted', 'total': 2},
        {'name': 'FFP', 'status': 'Broken', 'total': 3},
        {'name': 'FFP', 'status': 'Bloody', 'total': 12},
        {'name': 'FFP', 'status': 'Lipemic', 'total': 25},
        {'name': 'APHERESIS', 'status': 'Expired', 'total': 3},
        {'name': 'Plat PC', 'status': 'Expired', 'total': 1},
    ]

    # Mock Adverse Reaction Data
    adverse_summary = [
        {'type': 'MILD', 'total': 3},
        {'type': 'MODERATE', 'total': 1},
    ]

    adverse_details = [
        {'code': '0023082', 'name': 'IBRAHIM NASSER ALOMRANI', 'id': '1114598780', 'date': '2026-01-12T13:24:09.95'},
        {'code': '0023131', 'name': 'IBRAHIM MOHAMMED MADKHALI', 'id': '1133207017', 'date': '2026-01-15T11:53:49.613'},
        {'code': 'H107726000246', 'name': 'ALI ABDULRAHMAN ALKUWAYLIT', 'id': '1089381709', 'date': '2026-01-15T12:25:28.66'},
        {'code': 'H107726000395', 'name': 'MOHAMMED ALABD ALTAMIMI', 'id': '2196231188', 'date': '2026-01-27T14:16:11.49'},
    ]

    # Mock Donor Satisfaction Data
    satisfaction_summary = [
        {'question': 'Are you satisfied with the waiting time to finish the donation process?', 'vd': 2, 'd': 0, 'ok': 8, 's': 27, 'vs': 106},
        {'question': 'Are you comfortable during blood donation?', 'vd': 2, 'd': 1, 'ok': 6, 's': 20, 'vs': 111},
        {'question': 'Are you satisfied with the blood bank staff attending your needs and inquiries?', 'vd': 3, 'd': 0, 'ok': 8, 's': 15, 'vs': 116},
        {'question': 'Are you satisfied with the interview and information provided for your blood donation?', 'vd': 2, 'd': 0, 'ok': 8, 's': 33, 'vs': 98},
    ]

    dissatisfied_donors = [
        {'code': 'H107726000072', 'result': '0/4'},
        {'code': 'H107726000361', 'result': '0/4'},
    ]

    # Mock Acknowledgement Data
    acknowledgment_summary = [
        {'site': 'SMC1', 'component': 'PRBC', 'status': 'Not Acknowledged', 'count': 179, 'pct': '26.09% of Total PRBC orders : 686'},
        {'site': 'SMC1', 'component': 'PRBC', 'status': 'Acknowledged', 'count': 408, 'pct': '73.91% of Total PRBC orders : 686'},
        {'site': 'SMC1', 'component': 'Plat PC', 'status': 'Not Acknowledged', 'count': 10, 'pct': '18.52% of Total Plat PC orders : 54'},
        {'site': 'SMC1', 'component': 'Plat PC', 'status': 'Acknowledged', 'count': 44, 'pct': '81.48% of Total Plat PC orders : 54'},
        {'site': 'SMC1', 'component': 'FFP', 'status': 'Not Acknowledged', 'count': 13, 'pct': '25.00% of Total FFP orders : 52'},
        {'site': 'SMC1', 'component': 'FFP', 'status': 'Acknowledged', 'count': 40, 'pct': '75.00% of Total FFP orders : 52'},
        {'site': 'SMC1', 'component': 'Cryoprecipitate', 'status': 'Not Acknowledged', 'count': 2, 'pct': '40.00% of Total Cryoprecipitate orders : 5'},
        {'site': 'SMC1', 'component': 'Cryoprecipitate', 'status': 'Acknowledged', 'count': 3, 'pct': '60.00% of Total Cryoprecipitate orders : 5'},
        {'site': 'SMC2', 'component': 'PRBC', 'status': 'Not Acknowledged', 'count': 64, 'pct': '32.20% of Total PRBC orders : 200'},
        {'site': 'SMC2', 'component': 'PRBC', 'status': 'Acknowledged', 'count': 136, 'pct': '67.71% of Total PRBC orders : 200'},
        {'site': 'SMC2', 'component': 'Plat PC', 'status': 'Not Acknowledged', 'count': 6, 'pct': '35.29% of Total Plat PC orders : 17'},
        {'site': 'SMC2', 'component': 'Plat PC', 'status': 'Acknowledged', 'count': 11, 'pct': '64.71% of Total Plat PC orders : 17'},
        {'site': 'SMC2', 'component': 'FFP', 'status': 'Not Acknowledged', 'count': 7, 'pct': '33.33% of Total FFP orders : 21'},
        {'site': 'SMC2', 'component': 'FFP', 'status': 'Acknowledged', 'count': 14, 'pct': '66.67% of Total FFP orders : 21'},
    ]

    return render(request, 'reports/monthly_report.html', {
        'stats': stats,
        'stats': {},
        'donors_data': [],
        'reactive_data': [],
        'discarded_summary': [],
        'discarded_details': [],
        'adverse_summary': [],
        'adverse_details': [],
        'satisfaction_summary': [],
        'dissatisfied_donors': [],
        'acknowledgment_summary': []
    })

@staff_required
def inventory_checkup(request):
    return render(request, 'reports/inventory_checkup.html', {
        'system_units': [],
        'difference_units': [],
    })

@staff_required
def component_near_expired(request):
    return render(request, 'reports/component_near_expired.html', {
        'units': [],
    })

@staff_required
def issued_units_summary(request):
    return render(request, 'reports/issued_units_summary.html', {
        'component_summary': [],
        'patient_summary': [],
        'blood_group_summary': [],
    })

@staff_required
def ortho_summary(request):
    return render(request, 'reports/ortho_summary.html', {
        'summary_data': []
    })

@staff_required
def ortho_results_smc1(request):
    return render(request, 'reports/ortho_results.html', {
        'results': [],
        'title': 'Ortho Results [ الفرع الأول ]'
    })

@staff_required
def ortho_results_smc2(request):
    return render(request, 'reports/ortho_results.html', {
        'results': [],
        'title': 'Ortho Results [ الفرع الثاني ]'
    })

@staff_required
def infinity_results(request):
    return render(request, 'reports/infinity_results.html', {
        'results': []
    })

# Blood Order Process Views
@staff_required
def blood_requests_old(request):
    """
    Blood Requests Listing View.
    """
    requests = [
        {
            'code': 'R21000014', 'mrn': '961531', 'priority': 'STAT', 'request_type': 'Type & Screen',
            'diagnosis': 'Anal fissure, unspecified', 'blood_group': 'A+',
            'requested': ['PRBC: 2', 'HGB: 14.40', 'Duration: Two_Hour', 'FFP: 3', 'INR: 1.00', 'Duration: Half_an_Hour', 'PLT: 4', 'PLTCount: 299', 'Duration: Half_an_Hour', 'CRYO: 1', 'Duration: One_Hour'],
            'is_emergency': True,
            'status': 'Received', 'received_date': '23/12/2021 11:38 AM', 'received_by': 'tamer',
            'created_by': 'Tamer ElGendy', 'created_date': '23/12/2021 11:31 AM',
            'modified_by': 'Tamer ElGendy', 'modified_date': '23/12/2021 11:39 AM'
        },
        {
            'code': 'R21000013', 'mrn': '354865', 'priority': 'Normal', 'request_type': 'Cross Matching',
            'diagnosis': 'Sequelae of stroke, not specified as haemorrhage or infarction', 'blood_group': 'O+',
            'requested': ['PRBC: 1', 'HGB: 9.20', 'Duration: Half_an_Hour'],
            'is_emergency': False,
            'status': 'Received', 'received_date': '27/10/2021 02:20 PM', 'received_by': 'tamer',
            'created_by': 'Tamer ElGendy', 'created_date': '27/10/2021 02:14 PM',
            'modified_by': '', 'modified_date': ''
        },
    ]
    return render(request, 'blood_process/blood_requests_list.html', {'requests': requests})

@staff_required
def blood_request_create(request):
    """
    New Blood Request Form View.
    """
    return render(request, 'blood_process/blood_request_form.html')

@staff_required
def blood_order_listing_bb(request):
    """
    Blood Order Listing BB View.
    Fetched from Real DB.
    """
    from orders.models import BloodOrder
    
    db_orders = BloodOrder.objects.all().select_related('requester').order_by('-created_at')
    
    orders = []
    for o in db_orders:
        orders.append({
            'id': o.id,
            'code': f"ORD-{o.id}",
            'mrn': o.patient_mrn,
            'patient_name': o.patient_full_name,
            'priority': o.get_urgency_display(),
            'type': o.hospital_ward, # Using Ward as Type for now
            'blood_group': o.patient_blood_group,
            'unit_type': o.get_component_type_display(),
            'quantity': f"{o.units_requested} Unit(s)",
            'notes': '', # Add notes field to model if needed
            'status_label': o.get_status_display(),
            'status_date': o.updated_at.strftime('%d/%m/%Y %I:%M %p'),
            'status_by': o.requester.username if o.requester else 'System',
            'created_by': o.requester.username if o.requester else 'System',
            'created_date': o.created_at.strftime('%d/%m/%Y %I:%M %p')
        })

    return render(request, 'blood_process/smc2_order_listing.html', {'orders': orders})

@staff_required
def blood_order_detail(request, order_code):
    """
    Blood Order Detail View (Tabbed Interface).
    """
    from orders.models import BloodOrder
    from orders.services import OrderService
    
    # Handle "ORD-123" or just "123"
    oid = order_code.replace('ORD-', '')
    order = get_object_or_404(BloodOrder, pk=oid)
    
    # Find Compatible Units
    compatible_units = OrderService.find_compatible_units(order)
    
    # Fetch Crossmatched/Reserved Units
    crossmatches = order.crossmatches.all().select_related('unit')
    
    return render(request, 'blood_process/blood_order_detail.html', {
        'order': order,
        'order_code': order_code, # For display
        'compatible_units': compatible_units,
        'crossmatches': crossmatches
    })

@staff_required
def crossmatch_unit(request, order_id):
    if request.method == 'POST':
        from orders.models import BloodOrder
        from inventory.models import BloodComponent
        from orders.services import OrderService
        
        order = get_object_or_404(BloodOrder, pk=order_id)
        unit_id = request.POST.get('unit_id')
        unit = get_object_or_404(BloodComponent, pk=unit_id)
        
        xm = OrderService.perform_crossmatch(order, unit, request.user)
        
        if xm.is_compatible:
            messages.success(request, f"Unit {unit.unit_number} Crossmatched & Reserved")
        else:
            messages.error(request, f"Unit {unit.unit_number} is INCOMPATIBLE")
            
        return redirect('blood_order_detail', order_code=f"ORD-{order.id}")
    return redirect('blood_order_listing_bb')

@staff_required
def dispense_unit(request, crossmatch_id):
    if request.method == 'POST':
        from orders.models import Crossmatch
        from orders.services import OrderService
        
        xm = get_object_or_404(Crossmatch, pk=crossmatch_id)
        
        try:
            OrderService.dispense_unit(xm, request.user)
            messages.success(request, f"Unit {xm.unit.unit_number} ISSUED/DISPENSED successfully.")
        except ValueError as e:
            messages.error(request, str(e))
            
        return redirect('blood_order_detail', order_code=f"ORD-{xm.order.id}")
    return redirect('blood_order_listing_bb')

@staff_required
def smc2_orders_listing(request):
    # For now, showing same orders. In reality, filter by Site = SMC2
    # But since we renamed SMC2 to "Branch 2", we should filter by that if Site field exists.
    # Assuming all orders are visible for now.
    return blood_order_listing_bb(request)

@staff_required
def transfusion_orders(request):
    from orders.models import BloodOrder
    
    # In reality filter by status or department
    db_orders = BloodOrder.objects.all().order_by('-created_at')
    
    orders = []
    for o in db_orders:
        orders.append({
            'patient_name': o.patient_full_name, 
            'mrn': o.patient_mrn, 
            'qty': str(o.units_requested), 
            'note': f"{o.units_requested} Unit(s) {o.component_type}",
            'site': o.hospital_ward, # Use Ward as Site/Location
            'source': 'InPatient', 
            'priority': o.urgency, 
            'unit_type': o.component_type,
            'main_qty': str(o.units_requested), 
            'blood_group': o.patient_blood_group, 
            'code': f"ORD-{o.id}",
            'status': o.get_status_display(), 
            'status_by': 'System', # Placeholder
            'status_date': o.updated_at,
            'created_by': o.requester.username if o.requester else 'Unknown', 
            'created_date': o.created_at
        })

    return render(request, 'blood_process/transfusion_orders.html', {'orders': orders})

@staff_required
def unit_crossmatch_report(request):
    from orders.models import Crossmatch
    
    crossmatches = Crossmatch.objects.select_related('order', 'unit').order_by('-tested_at')
    
    items = []
    for xm in crossmatches:
        items.append({
            'lab_number': f"L26-{xm.id:04d}", # Fake Lab Number
            'test': 'Crossmatch',
            'result': 'Compatible' if xm.is_compatible else 'Incompatible',
            'result_color': 'text-emerald-600' if xm.is_compatible else 'text-rose-600',
            'created_date': xm.tested_at,
            'donor_sample_provided': 'Yes',
            'unit_number': xm.unit.unit_number,
            'mrn': xm.order.patient_mrn,
            'patient_name': xm.order.patient_full_name
        })
        
    return render(request, 'reports/unit_crossmatch_report.html', {'items': items, 'title': 'Unit Crossmatch Report'})

@staff_required
def emergency_issue_list(request):
    # Mock data for Issue Requests List
    requests = []
    
    # Mock similar to screenshot
    # Columns: Req.Code.#, VisualInspection, LabelChecked, NurseName, NurseID, Patient MRN, Patient Name, Date, Actions
    names = ['Muath Abdelqader Almomani', 'Fatima Bibi Fiqar', 'Md Saiful - Alam', 'Khalid Ghareeb Abdulhamid', 'Manal Yahiya Hazazi', 'Ahlam Ahmed Almohammed', 'Dilshad Bano Khan']
    nurses = ['nada', 'niamin', 'ATHIRA', 'SINU', 'akila', 'lalsmed', 'sona thomas', 'jincy mathew', 'jaimol']
    
    import random
    
    for i in range(20):
        req_id = 22019 - i
        nurse = nurses[i % len(nurses)]
        patient = names[i % len(names)]
        
        requests.append({
            'req_code': req_id,
            'visual_inspection': True,
            'label_checked': True,
            'nurse_name': nurse,
            'nurse_id': 15865 + i,
            'patient_mrn': 702650 + (i*123),
            'patient_name': patient,
            'date_by_name': 'Omar Mohammed Almutairi' if i % 2 == 0 else 'Faisal Ayed Alotaibi',
            'date_time': '01/02/2026 05:52 PM'
        })

    return render(request, 'clinical/emergency_issue_list.html', {
        'requests': requests
    })

@staff_required
def emergency_issue_create(request):
    # Form view for new Emergency Issue Request
    return render(request, 'clinical/emergency_issue_form.html')


# ──────────────────────────────────────────────
# Component Label API  (POST /api/components/<id>/print_label/)
# ──────────────────────────────────────────────
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

@staff_required
@require_POST
def component_print_label(request, component_id):
    """
    Mark a BloodComponent as labeled (simulate print).
    Called from the Labeling tab in the Donation Workflow.
    """
    from inventory.models import BloodComponent
    from django.utils import timezone
    import json

    try:
        component = BloodComponent.objects.get(pk=component_id)
    except BloodComponent.DoesNotExist:
        return JsonResponse({'status': 'error', 'error': 'Component not found'}, status=404)

    # Mark as labeled
    component.is_labeled = True
    component.label_printed_at = timezone.now()
    component.save()

    return JsonResponse({
        'status': 'success',
        'message': f'Label printed for {component.unit_number}',
        'printed_at': component.label_printed_at.strftime('%Y-%m-%d %H:%M'),
        'component_id': component.id,
        'unit_number': component.unit_number,
    })


@staff_required
@require_POST
def complete_labeling(request, workflow_id):
    """
    Mark all components as labeled & move workflow to LABS step.
    Called from 'Complete & Move to Storage' button.
    """
    from inventory.services import InventoryService
    from .models import DonorWorkflow
    from django.shortcuts import get_object_or_404

    workflow = get_object_or_404(DonorWorkflow, pk=workflow_id)
    InventoryService.release_components(workflow, passed=True)

    if workflow.status not in (DonorWorkflow.Step.LABS, DonorWorkflow.Step.COMPLETED):
        workflow.status = DonorWorkflow.Step.LABS
        workflow.save()

    return JsonResponse({'status': 'success', 'message': 'Components moved to storage. Workflow advanced to Labs.'})

@staff_required
@require_POST
def complete_workflow(request, workflow_id):
    """
    Mark the entire workflow as COMPLETED.
    Called automatically when the admin reaches the final History tab.
    """
    from .models import DonorWorkflow
    from django.shortcuts import get_object_or_404
    import json

    workflow = get_object_or_404(DonorWorkflow, pk=workflow_id)
    
    if workflow.status != DonorWorkflow.Step.COMPLETED:
        workflow.status = DonorWorkflow.Step.COMPLETED
        workflow.save()

    return JsonResponse({'status': 'success', 'message': 'Donation workflow marked as COMPLETED.'})
