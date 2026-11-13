from core.forms import form_for

from .models import Exam, ExamCandidate, ExamPaper, InvigilationDuty

ExamForm = form_for(Exam)
ExamPaperForm = form_for(ExamPaper)
ExamCandidateForm = form_for(ExamCandidate)
InvigilationDutyForm = form_for(InvigilationDuty)
