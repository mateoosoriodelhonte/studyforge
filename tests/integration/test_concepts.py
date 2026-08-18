"""Manual concept management.

Extraction produces candidates; these are the routes that let a learner correct
them. The course page tells the user they can edit or delete anything wrong, so
these tests exist to keep that claim true.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from studyforge.models import Concept, Course, ExtractionMethod
from studyforge.services import concepts as concept_service
from studyforge.services.exceptions import NotFoundError, ValidationError


@pytest.fixture
def course(db_session: Session) -> Course:
    db_session.add(course := Course(name="Data Structures"))
    db_session.commit()
    return course


class TestService:
    def test_creates_a_concept_marked_as_the_learners_own(
        self, db_session: Session, course: Course
    ) -> None:
        concept = concept_service.create_concept(
            db_session,
            course_id=course.id,
            name="Amortised analysis",
            definition="Averaging cost across a long sequence of operations.",
        )
        db_session.commit()
        assert concept.extraction_method is ExtractionMethod.MANUAL
        assert concept.normalized_name == "amortised analysis"
        assert concept.score == 1.0

    def test_a_duplicate_name_is_rejected_with_a_field_error(
        self, db_session: Session, course: Course
    ) -> None:
        concept_service.create_concept(db_session, course_id=course.id, name="AVL Tree")
        db_session.commit()
        with pytest.raises(ValidationError) as caught:
            concept_service.create_concept(db_session, course_id=course.id, name="avl trees")
        assert "name" in caught.value.field_errors

    def test_the_same_name_is_fine_in_another_course(self, db_session: Session) -> None:
        db_session.add_all([a := Course(name="A"), b := Course(name="B")])
        db_session.commit()
        concept_service.create_concept(db_session, course_id=a.id, name="Graph")
        concept_service.create_concept(db_session, course_id=b.id, name="Graph")
        db_session.commit()  # must not raise

    @pytest.mark.parametrize("name", ["", "   "])
    def test_a_blank_name_is_rejected(self, db_session: Session, course: Course, name: str) -> None:
        with pytest.raises(ValidationError) as caught:
            concept_service.create_concept(db_session, course_id=course.id, name=name)
        assert "name" in caught.value.field_errors

    def test_editing_marks_the_concept_as_the_learners_own(
        self, db_session: Session, course: Course
    ) -> None:
        """A concept the learner has corrected is theirs, not an extraction artefact."""
        db_session.add(
            concept := Concept(
                course_id=course.id,
                name="AVL tree",
                normalized_name="avl tree",
                extraction_method=ExtractionMethod.FREQUENCY,
            )
        )
        db_session.commit()
        concept_service.update_concept(
            db_session, concept.id, name="AVL tree", definition="A self-balancing BST."
        )
        db_session.commit()
        assert concept.extraction_method is ExtractionMethod.MANUAL
        assert concept.definition == "A self-balancing BST."

    def test_renaming_onto_an_existing_name_is_rejected(
        self, db_session: Session, course: Course
    ) -> None:
        concept_service.create_concept(db_session, course_id=course.id, name="Heap")
        second = concept_service.create_concept(db_session, course_id=course.id, name="Stack")
        db_session.commit()
        with pytest.raises(ValidationError):
            concept_service.update_concept(db_session, second.id, name="heap", definition=None)

    def test_renaming_a_concept_to_its_own_name_is_allowed(
        self, db_session: Session, course: Course
    ) -> None:
        concept = concept_service.create_concept(db_session, course_id=course.id, name="Heap")
        db_session.commit()
        concept_service.update_concept(
            db_session, concept.id, name="Heap", definition="A complete binary tree."
        )
        db_session.commit()  # must not raise

    def test_a_too_long_definition_is_rejected(self, db_session: Session, course: Course) -> None:
        with pytest.raises(ValidationError) as caught:
            concept_service.create_concept(
                db_session, course_id=course.id, name="Long", definition="x" * 5000
            )
        assert "definition" in caught.value.field_errors

    def test_deleting_removes_it(self, db_session: Session, course: Course) -> None:
        concept = concept_service.create_concept(db_session, course_id=course.id, name="Gone")
        db_session.commit()
        concept_service.delete_concept(db_session, concept.id)
        db_session.commit()
        assert db_session.get(Concept, concept.id) is None

    @pytest.mark.parametrize(
        "call",
        [
            lambda s: concept_service.get_concept(s, 9999),
            lambda s: concept_service.update_concept(s, 9999, name="x", definition=None),
            lambda s: concept_service.delete_concept(s, 9999),
        ],
    )
    def test_a_missing_concept_is_a_not_found(self, db_session: Session, call: object) -> None:
        with pytest.raises(NotFoundError):
            call(db_session)  # type: ignore[operator]

    def test_the_generator_projection_carries_provenance(
        self, db_session: Session, course: Course
    ) -> None:
        concept_service.create_concept(
            db_session, course_id=course.id, name="Heap", definition="A complete binary tree."
        )
        db_session.commit()
        [source] = concept_service.concept_sources(db_session, course.id)
        assert source.name == "Heap"
        assert source.has_definition


class TestThroughTheWeb:
    def test_the_full_add_edit_delete_journey(self, client: TestClient) -> None:
        course = client.post("/api/courses", json={"name": "DS"}).json()

        created = client.post(
            f"/courses/{course['id']}/concepts/new",
            data={"name": "Amortised analysis", "definition": "Averaging cost over time."},
        )
        assert created.status_code == 200
        assert "Amortised analysis" in created.text

        concept = client.get(f"/api/courses/{course['id']}/concepts").json()[0]
        edited = client.post(
            f"/concepts/{concept['id']}/edit",
            data={"name": "Amortised analysis", "definition": "Averaging cost across a run."},
        )
        assert edited.status_code == 200
        assert "Averaging cost across a run." in edited.text

        deleted = client.post(f"/concepts/{concept['id']}/delete")
        assert deleted.status_code == 200
        assert client.get(f"/api/courses/{course['id']}/concepts").json() == []

    def test_a_duplicate_name_re_renders_with_the_error_beside_the_field(
        self, client: TestClient
    ) -> None:
        course = client.post("/api/courses", json={"name": "DS"}).json()
        client.post(f"/courses/{course['id']}/concepts/new", data={"name": "Heap"})
        response = client.post(f"/courses/{course['id']}/concepts/new", data={"name": "heaps"})
        assert response.status_code == 422
        assert 'id="error-name"' in response.text
        assert 'aria-invalid="true"' in response.text

    def test_the_course_page_offers_editing_of_every_concept(self, client: TestClient) -> None:
        """The page claims concepts can be corrected; the links must be there."""
        course = client.post("/api/courses", json={"name": "DS"}).json()
        client.post(
            f"/api/courses/{course['id']}/documents/paste",
            json={
                "title": "T",
                "body": (
                    "An AVL tree is a self-balancing binary search tree that keeps "
                    "every balance factor within negative one, zero and one."
                ),
            },
        )
        body = client.get(f"/courses/{course['id']}").text
        concept = client.get(f"/api/courses/{course['id']}/concepts").json()[0]
        assert f"/concepts/{concept['id']}/edit" in body
        assert f"/courses/{course['id']}/concepts/new" in body

    def test_the_edit_form_shows_where_an_extracted_concept_came_from(
        self, client: TestClient
    ) -> None:
        course = client.post("/api/courses", json={"name": "DS"}).json()
        client.post(
            f"/api/courses/{course['id']}/documents/paste",
            json={
                "title": "T",
                "body": (
                    "An AVL tree is a self-balancing binary search tree that keeps "
                    "every balance factor within negative one, zero and one."
                ),
            },
        )
        concept = client.get(f"/api/courses/{course['id']}/concepts").json()[0]
        body = client.get(f"/concepts/{concept['id']}/edit").text
        assert "Where this came from" in body
        assert "extracted automatically" in body

    def test_a_missing_concept_gives_a_clean_404(self, client: TestClient) -> None:
        response = client.get("/concepts/99999/edit")
        assert response.status_code == 404
        assert "Traceback" not in response.text
