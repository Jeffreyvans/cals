"""Plain-English content for the public learn pages.

Each entry drives one page under the platform site. The words are deliberately
short and concrete: no statistics, client names, awards or testimonials, and no
prices. The pricing page asks for a demo instead of inventing a number.
"""

from config.brand import PRODUCT_NAME

LEARN_PAGES = [
    {
        "slug": "features",
        "name": "learn_features",
        "nav_label": "Features",
        "title": f"Features — {PRODUCT_NAME}",
        "description": (
            f"See how {PRODUCT_NAME} brings admissions, student records, fees, results, "
            "attendance, timetables and communication together in one system."
        ),
        "heading": "Everything your institution needs, in one calm place",
        "lead": (
            f"{PRODUCT_NAME} joins the office, the classroom and the family. Each part "
            "is switched on only when your institution needs it, so the system "
            "grows with you instead of getting in the way."
        ),
        "sections": [
            {
                "heading": "Students and admissions",
                "body": (
                    "Keep one clean student file from the first application to "
                    "graduation, with no second spreadsheet to reconcile."
                ),
                "cards": [
                    {
                        "icon": "user-plus",
                        "title": "Admissions",
                        "text": (
                            "Take applications, review them, record interviews, "
                            "issue offers and turn an accepted offer into a "
                            "student record."
                        ),
                    },
                    {
                        "icon": "users",
                        "title": "Student records",
                        "text": (
                            "Names, guardians, classes, programmes, documents and "
                            "notes in one place that the office can trust."
                        ),
                    },
                    {
                        "icon": "award",
                        "title": "Identity and progress",
                        "text": (
                            "Print identity cards, record enrolment and keep a "
                            "clear history of each student's time with you."
                        ),
                    },
                ],
            },
            {
                "heading": "Money and fees",
                "body": (
                    "Build the fee structure once, then let the system carry it "
                    "through invoices, payments, receipts and statements."
                ),
                "cards": [
                    {
                        "icon": "cash",
                        "title": "Fee structures",
                        "text": (
                            "Set fees by class, programme or year and let the "
                            "system work out what each student owes."
                        ),
                    },
                    {
                        "icon": "file",
                        "title": "Invoices and payments",
                        "text": (
                            "Issue invoices, record cash, bank, card or mobile "
                            "money payments, and keep a running balance."
                        ),
                    },
                    {
                        "icon": "check",
                        "title": "Receipts and statements",
                        "text": (
                            "Give every payment a numbered receipt and print a "
                            "statement that a family can follow."
                        ),
                    },
                ],
            },
            {
                "heading": "Teaching, attendance and results",
                "body": (
                    "The timetable, the register and the mark book sit together, "
                    "so the teacher's work reaches the office without retyping."
                ),
                "cards": [
                    {
                        "icon": "calendar",
                        "title": "Attendance",
                        "text": (
                            "Mark a register in seconds, spot repeated absences "
                            "and keep a record that is easy to review."
                        ),
                    },
                    {
                        "icon": "book",
                        "title": "Marks and results",
                        "text": (
                            "Enter marks, moderate them, release results and "
                            "print report cards and transcripts."
                        ),
                    },
                    {
                        "icon": "clock",
                        "title": "Timetables",
                        "text": (
                            "Keep class and teacher timetables in step and give "
                            "each person the version they need."
                        ),
                    },
                ],
            },
            {
                "heading": "Communication and the wider institution",
                "body": (
                    "Parents and students see the day-to-day picture, while the "
                    "office keeps the records behind it."
                ),
                "cards": [
                    {
                        "icon": "home",
                        "title": "Parent portal",
                        "text": (
                            "Parents switch between their children and see "
                            "results, attendance, balances and notices."
                        ),
                    },
                    {
                        "icon": "chart",
                        "title": "Reports",
                        "text": (
                            "Enrolment, debtors, collections and attendance in "
                            "plain charts a head can act on."
                        ),
                    },
                    {
                        "icon": "building",
                        "title": "School operations",
                        "text": (
                            "Library loans, boarding rolls, transport routes, "
                            "staff leave and procurement without extra systems."
                        ),
                    },
                ],
            },
        ],
        "cta": {
            "heading": "See it with your own records",
            "body": (
                "Tell us what your office struggles with and we will walk through "
                f"the parts of {PRODUCT_NAME} that fit your institution."
            ),
        },
    },
    {
        "slug": "for-schools",
        "name": "learn_for_schools",
        "nav_label": "For schools",
        "title": f"For schools — {PRODUCT_NAME}",
        "description": (
            f"{PRODUCT_NAME} for primary and secondary schools: class lists, "
            "attendance, report cards, fees, parent messages, boarding and transport."
        ),
        "heading": "For primary and secondary schools",
        "lead": (
            f"A school day moves quickly. {PRODUCT_NAME} keeps the register, the fees, "
            "the report cards and the messages to parents in one place, so the "
            "office can keep up without chasing paper."
        ),
        "sections": [
            {
                "heading": "The school day",
                "body": (
                    "Teachers get the lists and tools they need. The office sees "
                    "the same information without asking for it again."
                ),
                "cards": [
                    {
                        "icon": "calendar",
                        "title": "Registers and timetables",
                        "text": (
                            "Class lists, daily attendance and the timetable in "
                            "one view for teachers and the office."
                        ),
                    },
                    {
                        "icon": "book",
                        "title": "Marks and report cards",
                        "text": (
                            "Enter subject marks, review them and print a report "
                            "card that carries your school's name and logo."
                        ),
                    },
                    {
                        "icon": "users",
                        "title": "Student files",
                        "text": (
                            "Guardian details, class history, documents and "
                            "notes together in one student record."
                        ),
                    },
                ],
            },
            {
                "heading": "The school office",
                "body": (
                    "Fees, admissions and staff records are the work that never "
                    "stops. They should not need a separate spreadsheet for each."
                ),
                "cards": [
                    {
                        "icon": "cash",
                        "title": "Fees and receipts",
                        "text": (
                            "Set the fee structure, issue invoices, record "
                            "payments and give a numbered receipt every time."
                        ),
                    },
                    {
                        "icon": "user-plus",
                        "title": "Admissions",
                        "text": (
                            "Take applications, record decisions and create the "
                            "student record when an offer is accepted."
                        ),
                    },
                    {
                        "icon": "building",
                        "title": "Boarding and transport",
                        "text": (
                            "Hostel rolls, exeats, bus routes and pick-up lists "
                            "for the parts of school life beyond the classroom."
                        ),
                    },
                ],
            },
            {
                "heading": "Families",
                "body": (
                    "Parents want a clear answer to simple questions. The parent "
                    "portal gives it to them without a phone call to the office."
                ),
                "cards": [
                    {
                        "icon": "home",
                        "title": "Parent portal",
                        "text": (
                            "Parents see their children's results, attendance, "
                            "fees and school notices after signing in."
                        ),
                    },
                    {
                        "icon": "message",
                        "title": "Notices",
                        "text": (
                            "Send a notice to the right families and keep a "
                            "record of what was sent."
                        ),
                    },
                    {
                        "icon": "check",
                        "title": "Clear balances",
                        "text": (
                            "A family can see what has been paid, what is still "
                            "due and the receipts already issued."
                        ),
                    },
                ],
            },
        ],
        "cta": {
            "heading": "Built around your school day",
            "body": (
                "Ask for a walkthrough and we will show the screens a class "
                "teacher, a bursar and a head actually use."
            ),
        },
    },
    {
        "slug": "for-colleges",
        "name": "learn_for_colleges",
        "nav_label": "For colleges",
        "title": f"For colleges — {PRODUCT_NAME}",
        "description": (
            f"{PRODUCT_NAME} for colleges and universities: programmes, course "
            "registration, credit limits, marks, GPA, transcripts and fees."
        ),
        "heading": "For colleges and universities",
        "lead": (
            "A college has to track programmes, course choices, credits and "
            f"results across several years. {PRODUCT_NAME} keeps that history together "
            "and gives each student a clear view of where they stand."
        ),
        "sections": [
            {
                "heading": "Programmes and registration",
                "body": (
                    "Students choose courses, the system checks the rules, and "
                    "the record stays consistent from enrolment to graduation."
                ),
                "cards": [
                    {
                        "icon": "building",
                        "title": "Programmes and years",
                        "text": (
                            "Set up programmes, year levels and the courses that "
                            "belong to each one."
                        ),
                    },
                    {
                        "icon": "user-plus",
                        "title": "Course registration",
                        "text": (
                            "Let students register for courses within credit "
                            "limits and keep the final list in one place."
                        ),
                    },
                    {
                        "icon": "calendar",
                        "title": "Timetables",
                        "text": (
                            "Lecturer and student timetables that follow the "
                            "courses actually registered."
                        ),
                    },
                ],
            },
            {
                "heading": "Teaching and assessment",
                "body": (
                    "Marks move from the lecturer to the examiner to the student "
                    "record with a clear record of who released them."
                ),
                "cards": [
                    {
                        "icon": "book",
                        "title": "Course sites",
                        "text": (
                            "Materials, assignments, quizzes and forums beside "
                            "the course roster."
                        ),
                    },
                    {
                        "icon": "award",
                        "title": "Marks and GPA",
                        "text": (
                            "Record continuous assessment and exams, then work "
                            "out grades and GPA using your rules."
                        ),
                    },
                    {
                        "icon": "file",
                        "title": "Transcripts",
                        "text": (
                            "Print a transcript or certificate that carries your "
                            "institution's logo and current details."
                        ),
                    },
                ],
            },
            {
                "heading": "Student records and finance",
                "body": (
                    "The registrar and the bursar see the same student, so a "
                    "registration hold and a fee balance never disagree."
                ),
                "cards": [
                    {
                        "icon": "users",
                        "title": "Student records",
                        "text": (
                            "Admission details, programme history, documents and "
                            "contact information in one file."
                        ),
                    },
                    {
                        "icon": "cash",
                        "title": "Fees and statements",
                        "text": (
                            "Invoices, payments, receipts and statements for "
                            "each student or sponsor."
                        ),
                    },
                    {
                        "icon": "chart",
                        "title": "Reports",
                        "text": (
                            "Enrolment, debtors, results and course uptake in "
                            "reports the senate can read."
                        ),
                    },
                ],
            },
        ],
        "cta": {
            "heading": "A transcript is only useful if the record is right",
            "body": (
                "Walk us through your programme rules and we will show how "
                f"{PRODUCT_NAME} keeps registration, marks and graduation in step."
            ),
        },
    },
    {
        "slug": "for-parents-and-students",
        "name": "learn_for_parents_and_students",
        "nav_label": "Parents and students",
        "title": f"For parents and students — {PRODUCT_NAME}",
        "description": (
            f"What parents and students can see in the {PRODUCT_NAME} portal: results, "
            "attendance, fees, receipts, learning materials and school notices."
        ),
        "heading": "For parents and students",
        "lead": (
            "The portal is the family's window into the institution. It answers "
            "the usual questions without a visit to the office: how is my child "
            "doing, what is still owed, and what did the school send home?"
        ),
        "sections": [
            {
                "heading": "What you can see",
                "body": (
                    "Every family sees only their own children or their own "
                    "record. The institution decides what is published."
                ),
                "cards": [
                    {
                        "icon": "award",
                        "title": "Results",
                        "text": (
                            "Marks and report cards appear when the institution "
                            "releases them."
                        ),
                    },
                    {
                        "icon": "calendar",
                        "title": "Attendance",
                        "text": (
                            "See the days marked present, absent or late, and "
                            "any note the school added."
                        ),
                    },
                    {
                        "icon": "cash",
                        "title": "Fees and receipts",
                        "text": (
                            "See the balance, what has been paid and the receipt "
                            "for each payment."
                        ),
                    },
                    {
                        "icon": "book",
                        "title": "Learning materials",
                        "text": (
                            "Course notes, assignments and messages appear in "
                            "the learning area when a teacher shares them."
                        ),
                    },
                    {
                        "icon": "message",
                        "title": "Notices",
                        "text": (
                            "School messages and reminders arrive with the rest "
                            "of your account information."
                        ),
                    },
                    {
                        "icon": "user-plus",
                        "title": "Registration",
                        "text": (
                            "If your institution allows it, you can register for "
                            "a portal account from its public page."
                        ),
                    },
                ],
            },
            {
                "heading": "Signing in",
                "body": (
                    "Your institution gives you the sign-in link or shows it on "
                    "its public page. Use the username your institution created "
                    "for you."
                ),
                "list": [
                    "A student signs in with their own student account.",
                    "A parent signs in with the parent account linked to their children.",
                    "Staff sign in with the account their institution created.",
                    "If you forget your password, use the reset link on the sign-in page.",
                ],
            },
            {
                "heading": "Your privacy",
                "body": (
                    f"Your records belong to your institution. {PRODUCT_NAME} stores "
                    "and processes them on the institution's behalf, and only "
                    "the people with the right role can open them."
                ),
            },
        ],
        "cta": {
            "heading": "Not sure where to sign in?",
            "body": (
                "Open your institution's public page and use the sign-in card "
                "there, or ask the office for the link."
            ),
        },
    },
    {
        "slug": "security-and-privacy",
        "name": "learn_security_and_privacy",
        "nav_label": "Security and privacy",
        "title": f"Security and privacy — {PRODUCT_NAME}",
        "description": (
            f"How {PRODUCT_NAME} keeps one institution's records separate, limits who "
            "can see what, and gives every important change a dated record."
        ),
        "heading": "Your records stay private",
        "lead": (
            f"{PRODUCT_NAME} is built for one institution at a time. The system asks "
            "who a person is and which institution they belong to before it "
            "shows a record, and it keeps a dated record of the work that matters."
        ),
        "sections": [
            {
                "heading": "One institution at a time",
                "body": (
                    "Every student, payment and message belongs to one "
                    "institution. A person signed in at one institution cannot "
                    "open another institution's records."
                ),
            },
            {
                "heading": "Who can see what",
                "body": (
                    "Roles decide what each person can open. A class teacher, a "
                    "bursar and a registrar need different things, so they get "
                    "different screens. A student sees their own record; a parent "
                    "sees the children linked to their account."
                ),
                "list": [
                    "Permission checks run on screens and API requests.",
                    "Sensitive views are logged for review.",
                    "Administrators can change a role without changing the code.",
                ],
            },
            {
                "heading": "A record of what changed",
                "body": (
                    "Money, marks, users and roles leave a dated audit entry. "
                    "When two people disagree about what happened, the record "
                    "shows who did what and when."
                ),
            },
            {
                "heading": "Everyday protection",
                "body": (
                    "Passwords are stored as hashes, not as readable text. Public "
                    "forms are rate-limited, uploaded files are checked and "
                    "re-encoded, and the private application is kept out of "
                    "search results."
                ),
                "list": [
                    "Sign-in attempts are counted and an account can be locked temporarily.",
                    "Uploaded logos are checked and stripped of hidden metadata.",
                    "Backups and restore steps are documented for the institution.",
                ],
            },
            {
                "heading": "Your choices",
                "body": (
                    "The institution decides what is collected and why. If you "
                    "have a question about a student or staff record, start with "
                    "the institution's office. Read the full privacy notice for "
                    "the details."
                ),
            },
        ],
        "cta": {
            "heading": "Ask us a security question",
            "body": (
                "If your institution needs a specific answer before it can "
                "decide, tell us what you need to know."
            ),
        },
    },
    {
        "slug": "pricing-and-contact",
        "name": "learn_pricing_and_contact",
        "nav_label": "Pricing and contact",
        "title": f"Pricing and contact — {PRODUCT_NAME}",
        "description": (
            f"{PRODUCT_NAME} does not publish a price list. Request a demo and tell us "
            "about your institution, or contact us by phone, WhatsApp or email."
        ),
        "heading": "Talk to us about your institution",
        "lead": (
            "We do not publish a price list because the right setup depends on "
            "the size of your institution and the parts you need. Tell us what "
            "you are trying to fix and we will give you a clear answer."
        ),
        "sections": [
            {
                "heading": "How pricing works",
                "body": (
                    f"{PRODUCT_NAME} is priced for the institution, not for each "
                    "screen. We look at the number of students and staff, the "
                    "modules you will use and the support you need, then send a "
                    "written proposal."
                ),
                "list": [
                    "No price is invented on this page: ask for a proposal.",
                    "You can start with a few modules and add more later.",
                    "The proposal explains what is included and what is not.",
                ],
            },
            {
                "heading": "What a demo covers",
                "body": (
                    "A demo is a conversation with your own questions. We can "
                    "show admissions, fees, results, attendance, the parent "
                    "portal or any other part you want to see."
                ),
                "list": [
                    "Tell us the type of institution and the work that takes the most time.",
                    "We walk through the screens that match that work.",
                    "You leave with a clear next step, not a sales script.",
                ],
            },
        ],
        "cta": None,
    },
    {
        "slug": "faq",
        "name": "learn_faq",
        "nav_label": "FAQ",
        "title": f"Frequently asked questions — {PRODUCT_NAME}",
        "description": (
            f"Answers to common questions about {PRODUCT_NAME}: what it does, who owns "
            "the data, signing in, phones, fees, setup and privacy."
        ),
        "heading": "Questions people ask",
        "lead": (
            "Short answers to the questions that come up most often. If your "
            "question is not here, ask us directly."
        ),
        "sections": [],
        "faqs": [
            {
                "q": f"What is {PRODUCT_NAME}?",
                "a": (
                    f"{PRODUCT_NAME} is a school management system for schools, "
                    "colleges and vocational institutes. It brings admissions, "
                    "student records, fees, results, attendance, timetables and "
                    "communication into one place."
                ),
            },
            {
                "q": "Is it only for large institutions?",
                "a": (
                    "No. A small school can switch on the parts it needs and add "
                    "more later. The same system is used by primary schools, "
                    "secondary schools, colleges and vocational institutes."
                ),
            },
            {
                "q": "Who owns the data?",
                "a": (
                    "Your institution decides what is collected and why. "
                    f"{PRODUCT_NAME} stores and processes the records on the "
                    "institution's behalf. Read the privacy notice for the full "
                    "explanation."
                ),
            },
            {
                "q": "Does it work on a phone?",
                "a": (
                    "Yes. The public pages and the signed-in workspace are built "
                    "to work on a phone as well as a desktop, including on a "
                    "slow connection."
                ),
            },
            {
                "q": "How do parents sign in?",
                "a": (
                    "The institution creates a parent account and links it to the "
                    "children. The parent signs in on the institution's public "
                    "page or sign-in link and sees only those children."
                ),
            },
            {
                "q": "Can students register themselves?",
                "a": (
                    "Only when the institution allows it. A student can activate "
                    "a portal account when their admission details match, or use "
                    "a sign-up link the institution shared. Otherwise the office "
                    "creates the account."
                ),
            },
            {
                "q": "What does it cost?",
                "a": (
                    "We do not publish a price list because the setup depends on "
                    "the institution and the modules it needs. Request a demo and "
                    "we will send a written proposal."
                ),
            },
            {
                "q": "How long does setup take?",
                "a": (
                    "It depends on how much data you are bringing across. The "
                    "setup wizard creates the academic year, classes, roles and "
                    "first administrator, and the importer can bring in students "
                    "and staff from a spreadsheet."
                ),
            },
            {
                "q": "Can we keep our own logo and colours?",
                "a": (
                    "Yes. The institution's logo appears in the workspace, on "
                    "documents and on its public page. Its primary and accent "
                    "colours are used where they stay readable."
                ),
            },
            {
                "q": "Where do I report a problem?",
                "a": (
                    "Contact the institution's office first if the problem is "
                    "about a student or staff record. For a platform problem, use "
                    "the contact details on the pricing and contact page."
                ),
            },
        ],
        "cta": {
            "heading": "Still have a question?",
            "body": (
                "Send it to us and we will answer in plain English, without a "
                "sales script."
            ),
        },
    },
]

LEARN_BY_SLUG = {page["slug"]: page for page in LEARN_PAGES}
LEARN_BY_NAME = {page["name"]: page for page in LEARN_PAGES}
