from django.urls import path

from . import views as v

app_name = "admissions"

urlpatterns = [
    path("apply/", v.application_start, name="application_start"),
    path("apply/done/<uuid:token>/", v.application_success,
         name="application_success"),
    path("track/<uuid:token>/", v.track_application, name="track_application"),

    path("offer/<uuid:token>/accept/", v.offer_accept, name="offer_accept"),
    path("offer/<uuid:token>/letter.pdf", v.offer_letter, name="offer_letter"),

    path("verify/certificate/<uuid:token>/", v.verify_certificate,
         name="verify_certificate"),
    path("verify/card/<uuid:token>/", v.verify_card, name="verify_card"),
    path("me/id-card/pdf/", v.my_id_card_pdf, name="my_id_card_pdf"),

    path("applications/", v.application_list, name="application_list"),
    path("applications/<int:pk>/", v.application_detail,
         name="application_detail"),
    path("applications/<int:pk>/review/", v.application_review,
         name="application_review"),
    path("applications/<int:pk>/interview/", v.application_interview,
         name="application_interview"),
    path("applications/<int:pk>/offer/", v.application_offer,
         name="application_offer"),
    path("applications/<int:pk>/eligibility/", v.application_eligibility,
         name="application_eligibility"),
    path("applications/<int:pk>/documents/upload/", v.application_upload,
         name="application_upload"),
    path("applications/<int:pk>/documents/<int:document_pk>/",
         v.application_document, name="application_document"),

    path("offers/", v.offer_list, name="offer_list"),
    path("offers/<int:pk>/", v.offer_detail, name="offer_detail"),
    path("offers/<int:pk>/pdf/", v.offer_pdf, name="offer_pdf"),

    path("requirements/", v.entry_requirements, name="entry_requirements"),
    path("requirements/<int:programme_pk>/", v.entry_requirement_edit,
         name="entry_requirement_edit"),

    path("students/", v.lifecycle_list, name="lifecycle_list"),
    path("students/<int:pk>/", v.lifecycle_detail, name="lifecycle_detail"),
    path("students/<int:pk>/transition/", v.lifecycle_transition,
         name="lifecycle_transition"),
    path("students/<int:pk>/transfer/", v.student_transfer,
         name="student_transfer"),

    path("graduation/", v.graduation_list, name="graduation_list"),
    path("graduation/<int:pk>/", v.graduation_detail, name="graduation_detail"),
    path("graduation/<int:pk>/clearance/", v.graduation_clearance,
         name="graduation_clearance"),
    path("graduation/<int:pk>/graduate/", v.graduation_graduate,
         name="graduation_graduate"),
    path("graduation/<int:pk>/certificate/", v.certificate_new,
         name="certificate_new"),

    path("certificates/", v.certificate_list, name="certificate_list"),
    path("certificates/<int:pk>/", v.certificate_detail,
         name="certificate_detail"),
    path("certificates/<int:pk>/pdf/", v.certificate_pdf,
         name="certificate_pdf"),

    path("cards/", v.id_cards, name="id_cards"),
    path("cards/print/", v.id_card_bulk, name="id_card_bulk"),
    path("cards/<int:pk>/pdf/", v.id_card_pdf, name="id_card_pdf"),
    path("students/<int:pk>/card/", v.id_card_issue, name="id_card_issue"),

    path("alumni/", v.alumni_list, name="alumni_list"),
    path("alumni/<int:pk>/", v.alumni_detail, name="alumni_detail"),
]
