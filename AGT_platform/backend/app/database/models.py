from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey, Boolean, Numeric, JSON, Index, UUID
)
from sqlalchemy.orm import relationship, DeclarativeBase
from datetime import datetime
import uuid
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

class Base(DeclarativeBase):
    pass

def now():
    """
    This function returns the current date and time.
    """
    return datetime.now()

class AssignmentUpload(Base):
    """
    This model is used to store the assignment uploads.

    Parameters:
        filename: The filename of the assignment upload.
        storage_uri: The storage URI of the assignment upload.
        status: The status of the assignment upload.
        suggested_grade: The suggested grade of the assignment upload.
        feedback: The feedback of the assignment upload.
        created_at: The date and time the assignment upload was created.
        updated_at: The date and time the assignment upload was last updated.

    Relationships:
        assignment: The assignment that the upload belongs to.
    """

    __tablename__ = "assignment_uploads"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String(512), nullable=False)
    storage_uri = Column(Text, nullable=False)
    # MAKE THIS INTO AN ENUM!!
    status = Column(String(32), nullable=False, default="uploaded")  # uploaded|queued|grading|graded|error
    suggested_grade = Column(Float, nullable=True)
    feedback = Column(Text, nullable=True)

    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

class Assignment(Base):
    """
    This model is used to store the assignments.

    Parameters:
        course_id: The ID of the course that the assignment belongs to.
        title: The title of the assignment.
        description: The description of the assignment.
        modality: The modality of the assignment.
        rubric: The rubric of the assignment.
        created_at: The date and time the assignment was created.
        due_date: The date and time the assignment is due.
        grader_rubric_text: The text of the grader rubric.
        grader_answer_key_text: The text of the grader answer key.
        grader_instructions: The instructions for grading the assignment.

    Relationships:
        course: The course that the assignment belongs to.
        attachments: The attachments of the assignment.
    """
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True)
    # Null when the assignment is created by the public standalone autograder (no course).
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=True)
    title = Column(String, nullable=False)
    description = Column(Text)
    modality = Column(String, nullable=False)
    rubric = Column(JSON, nullable=False)
    created_at = Column(DateTime)
    due_date = Column(DateTime, nullable=True)
    # Optional text context for public autograder rows (course_id IS NULL); used by grade_submission.
    grader_rubric_text = Column(Text, nullable=True)
    grader_answer_key_text = Column(Text, nullable=True)
    grader_instructions = Column(Text, nullable=True)
    # Extracted plaintext of the uploaded blank assignment template (Assignment Creation flow);
    # lets the review page show the full original document alongside the parsed Q&A chunks.
    blank_assignment_text = Column(Text, nullable=True)

    course = relationship("Course")
    attachments = relationship("AssignmentAttachment", back_populates="assignment")
    question_chunks = relationship(
        "AssignmentQuestionChunk",
        back_populates="assignment",
        cascade="all, delete-orphan",
        order_by="AssignmentQuestionChunk.order_index",
    )


class AssignmentAttachment(Base):
    """
    This model is used to store the attachments of an assignment.
    
    Parameters:
        assignment_id: The ID of the assignment that the attachment belongs to.
        kind: The kind of attachment.
        object_key: The object key of the attachment.
        filename: The filename of the attachment.
        uploaded_by_id: The ID of the user who uploaded the attachment.
        created_at: The date and time the attachment was created.

    Relationships:
        assignment: The assignment that the attachment belongs to.
        uploaded_by: The user who uploaded the attachment.
    """

    __tablename__ = "assignment_attachments"

    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    kind = Column(String(32), nullable=False)  # rubric | answer_key | blank_assignment
    object_key = Column("s3_key", String(1024), nullable=False)
    filename = Column(String(512), nullable=False)
    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    assignment = relationship("Assignment", back_populates="attachments")
    uploaded_by = relationship("User")


class AssignmentQuestionChunk(Base):
    """
    One question/answer pair parsed (and optionally teacher-edited) from an Assignment's
    uploaded blank template + answer key. Produced by the Assignment Creation flow's parsing
    + chunking agents (see ``app.grading.parsing.assignment_context_parser`` and
    ``app.grading.chunking.assignment_qa_chunker``); teachers can edit and re-save these via
    ``app.routes.assignment_library``.

    Parameters:
        assignment_id: The ID of the assignment this chunk belongs to.
        question_id: Stable per-assignment label for this question (e.g. "1", "2a", "q3").
        order_index: Display / grading order among the assignment's chunks.
        question_text: The isolated question/prompt text.
        answer_text: The reference/expected answer text for this question.
        rubric_criteria: JSON list of rubric criterion rows selected for this question.
        is_edited: Whether a teacher has manually created or edited this chunk since parsing.
        created_at: The date and time the chunk was first created.
        updated_at: The date and time the chunk was last updated.

    Relationships:
        assignment: The assignment this chunk belongs to.
    """

    __tablename__ = "assignment_question_chunks"

    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    question_id = Column(String(120), nullable=False, default="")
    order_index = Column(Integer, nullable=False, default=0)
    question_text = Column(Text, nullable=False, default="")
    answer_text = Column(Text, nullable=False, default="")
    rubric_criteria = Column(JSON, nullable=True)
    is_edited = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    assignment = relationship("Assignment", back_populates="question_chunks")


class User(Base):
    """
    This model is used to store the users.

    Parameters:
        email: The email of the user.
        name: The name of the user.
        role: The role of the user.
        created_at: The date and time the user was created

    Relationships:
        issued_jwts: The issued JWT tokens of the user.
        refresh_tokens: The refresh tokens of the user.
        enrollments: The enrollments of the user.
        submissions: The submissions of the user.
        standalone_submissions: The standalone submissions of the user.
        ai_scores: The AI scores of the user.
    """
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String)
    role = Column(String, nullable=False)  # student|teacher|admin
    created_at = Column(DateTime, default=datetime.utcnow)
    # Local login (OAuth users leave password_hash null)
    password_hash = Column(String(255), nullable=True)
    institution_domain = Column(String(255), nullable=True)
    first_login_at = Column(DateTime, nullable=True)
    last_login_at = Column(DateTime, nullable=True)


class IssuedJwt(Base):
    """
    This model is used to store the issued JWT tokens.

    Parameters:
        user_id: The ID of the user who issued the JWT token.
        jti: The JWT token identifier.
        expires_at: The date and time the JWT token expires.
        revoked_at: The date and time the JWT token was revoked.
    """
    __tablename__ = "issued_jwts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    jti = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Course(Base):
    """
    This model is used to store the courses.

    Parameters:
        code: The code of the course.
        title: The title of the course.
        description: The description of the course.

    Relationships:
        enrollments: The enrollments of the course.
        assignments: The assignments of the course.
    """

    __tablename__ = "courses"
    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)

class Enrollment(Base):
    """
    This model is used to store the enrollments of a course.

    Parameters:
        course_id: The ID of the course that the enrollment belongs to.
        user_id: The ID of the user who is enrolled in the course.
        role: The role of the user in the course.

    Relationships:
        course: The course that the enrollment belongs to.
        user: The user who is enrolled in the course.
    """
    __tablename__ = "enrollments"
    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String, nullable=False)  # student|teacher

    course = relationship("Course")
    user = relationship("User")

Index("ix_enroll_course_user", Enrollment.course_id, Enrollment.user_id, unique=True)

class Submission(Base):

    """
    Lifecycle (status):
      uploading → uploaded → queued → grading → graded | needs_review | error
    Direct object-store flow: create as uploading; after browser PUTs to MinIO, finalize sets uploaded,
    then atomically queued + single Celery enqueue (grading_dispatch_at set once).

    Parameters:
        assignment_id: The ID of the assignment that the submission belongs to.
        student_id: The ID of the student who submitted the assignment.
        status: The status of the submission.
        created_at: The date and time the submission was created.
        updated_at: The date and time the submission was last updated.
        grading_dispatch_at: The date and time the submission was dispatched for grading.
        grading_celery_task_id: The ID of the Celery task that is grading the submission.
    
    Relationships:
        assignment: The assignment that the submission belongs to.
        student: The student who submitted the assignment.
        artifacts: The artifacts of the submission.
    """

    __tablename__ = "submissions"
    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    # Null for anonymous public autograder uploads; set when a JWT is present at upload time.
    student_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String(32), nullable=False, default="uploading")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Set once when grade_submission.delay succeeds — idempotent finalize / enqueue.
    grading_dispatch_at = Column(DateTime, nullable=True)
    grading_celery_task_id = Column(String(128), nullable=True)

    final_score = Column(Numeric(5, 2))
    final_feedback = Column(Text)
    # Best-effort client IP for anonymous autograder rate limiting / mutation checks.
    submitter_ip = Column(String(64), nullable=True)

    grading_report_object_key = Column("grading_report_s3_key", String(1024), nullable=True)

    assignment = relationship("Assignment")
    student = relationship("User")
    artifacts = relationship("SubmissionArtifact", back_populates="submission")

class SubmissionArtifact(Base):

    """
    This model is used to store the artifacts of a submission.
    This is used for submissions that are tied to a course or assignment (not standalone submissions).

    Parameters:
        submission_id: The ID of the submission that the artifact belongs to.
        kind: The kind of artifact.
        object_key: The object key of the artifact.
        sha256: The SHA-256 hash of the artifact.
        created_at: The date and time the artifact was created.

    Relationships:
        submission: The submission that the artifact belongs to.
    """

    __tablename__ = "submission_artifacts"
    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    kind = Column(String, nullable=False)  # pdf|txt|ipynb|zip|mp4|png|jpg
    object_key = Column("s3_key", String, nullable=False)
    sha256 = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    submission = relationship("Submission", back_populates="artifacts")

class AIScore(Base):

    """
    This model is used to store the AI scores of a submission.
    This is used for submissions that are tied to a course or assignment.

    Parameters:
        submission_id: The ID of the submission that the AI score belongs to.
        criterion: The criterion of the AI score.
        score: The score of the AI score.
        confidence: The confidence of the AI score.
        rationale: The rationale of the AI score.
        evidence: The evidence of the AI score.
        model: The model that was used to score the submission.
        created_at: The date and time the AI score was created.

    Relationships:
        submission: The submission that the AI score belongs to.
    """

    __tablename__ = "ai_scores"
    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    criterion = Column(String, nullable=False)
    score = Column(Numeric(5,2), nullable=False)
    confidence = Column(Numeric(3,2), nullable=False)
    rationale = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=True)
    model = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class AuditLog(Base):
    """ 
    This model is used to log audit events for the audit log.
    
    Parameters:
        actor_user_id: The ID of the user who performed the action.
        action: The action that was performed.
        target_type: The type of the target of the action.
        target_id: The ID of the target of the action.
        event_metadata: The metadata of the event.
    """
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String, nullable=False)  # VIEW_SUBMISSION, OVERRIDE_GRADE, etc.
    target_type = Column(String, nullable=False)  # Submission, Assignment, User
    target_id = Column(Integer, nullable=False)
    # metadata = Column(JSON, default=dict)
    event_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class StandaloneSubmission(Base):
    """
    Standalone autograder submission — not tied to a course or assignment.

    Parameters:
        user_id: The ID of the user who submitted the assignment.
        title: The title of the assignment.
        status: The status of the assignment.
        created_at: The date and time the assignment was created.
        updated_at: The date and time the assignment was last updated.
        grading_dispatch_at: The date and time the assignment was dispatched for grading.
        grading_celery_task_id: The ID of the Celery task that is grading the assignment.
        final_score: The final score of the assignment.
        final_feedback: The final feedback of the assignment.
        rubric_text: The text of the rubric.
        answer_key_text: The text of the answer key.
        grading_instructions: The instructions for grading the assignment.
        grading_report_object_key: The object key of the grading report.

    Relationships:
        user: The user who submitted the assignment.
        artifacts: The artifacts of the assignment.
        scores: The scores of the assignment.
    """

    __tablename__ = "standalone_submissions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(512), nullable=False, default="Untitled")
    status = Column(String(32), nullable=False, default="uploading")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    grading_dispatch_at = Column(DateTime, nullable=True)
    grading_celery_task_id = Column(String(128), nullable=True)

    final_score = Column(Numeric(5, 2), nullable=True)
    final_feedback = Column(Text, nullable=True)

    rubric_text = Column(Text, nullable=True)
    answer_key_text = Column(Text, nullable=True)
    # Optional free-text prompt (focus, learning goals) combined with rubric / sample in the grader.
    grading_instructions = Column(Text, nullable=True)

    grading_report_object_key = Column("grading_report_s3_key", String(1024), nullable=True)

    user = relationship("User")
    artifacts = relationship(
        "StandaloneArtifact", back_populates="submission", cascade="all, delete-orphan"
    )
    scores = relationship(
        "StandaloneAIScore", back_populates="submission", cascade="all, delete-orphan"
    )


class StandaloneArtifact(Base):
    """
    This model is used to store the artifacts of a standalone submission.
    This is used for standalone submissions that are not tied to a course or assignment.
    
    Parameters:
        submission_id: The ID of the submission that the artifact belongs to.
        kind: The kind of artifact.
        object_key: The object key of the artifact.
        filename: The filename of the artifact.
        sha256: The SHA-256 hash of the artifact.
        created_at: The date and time the artifact was created.
    
    Relationships:
        submission: The submission that the artifact belongs to.
    """
    __tablename__ = "standalone_artifacts"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("standalone_submissions.id"), nullable=False)
    kind = Column(String(32), nullable=False)
    object_key = Column("s3_key", String(1024), nullable=False)
    filename = Column(String(512), nullable=False)
    sha256 = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    submission = relationship("StandaloneSubmission", back_populates="artifacts")


class StandaloneAIScore(Base):

    """
    This model is used to store the AI scores of a standalone submission.

    Parameters:
        submission_id: The ID of the submission that the AI score belongs to.
        criterion: The criterion of the AI score.
        score: The score of the AI score.
        confidence: The confidence of the AI score.
        rationale: The rationale of the AI score.
        evidence: The evidence of the AI score.
        model: The model that was used to score the submission.
        created_at: The date and time the AI score was created.

    Relationships:
        submission: The submission that the AI score belongs to.
    """
    __tablename__ = "standalone_ai_scores"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("standalone_submissions.id"), nullable=False)
    criterion = Column(String, nullable=False)
    score = Column(Numeric(5, 2), nullable=False)
    confidence = Column(Numeric(3, 2), nullable=False)
    rationale = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=True)
    model = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    submission = relationship("StandaloneSubmission", back_populates="scores")
