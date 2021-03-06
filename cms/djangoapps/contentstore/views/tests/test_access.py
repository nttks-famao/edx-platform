"""
Tests access.py
"""
from django.test import TestCase
from django.contrib.auth.models import User
from xmodule.modulestore import Location
from xmodule.modulestore.locator import CourseLocator

from student.roles import CourseInstructorRole, CourseStaffRole
from student.tests.factories import AdminFactory
from student.auth import add_users
from contentstore.views.access import get_user_role


class RolesTest(TestCase):
    """
    Tests for user roles.
    """
    def setUp(self):
        """ Test case setup """
        self.global_admin = AdminFactory()
        self.instructor = User.objects.create_user('testinstructor', 'testinstructor+courses@edx.org', 'foo')
        self.staff = User.objects.create_user('teststaff', 'teststaff+courses@edx.org', 'foo')
        self.location = Location('i4x', 'mitX', '101', 'course', 'test')
        self.locator = CourseLocator(url='edx://mitX.101.test')

    def test_get_user_role_instructor(self):
        """
        Verifies if user is instructor.
        """
        add_users(self.global_admin, CourseInstructorRole(self.location), self.instructor)
        self.assertEqual(
            'instructor',
            get_user_role(self.instructor, self.location, self.location.course_id)
        )

    def test_get_user_role_instructor_locator(self):
        """
        Verifies if user is instructor, using a CourseLocator.
        """
        add_users(self.global_admin, CourseInstructorRole(self.locator), self.instructor)
        self.assertEqual(
            'instructor',
            get_user_role(self.instructor, self.locator)
        )

    def test_get_user_role_staff(self):
        """
        Verifies if user is staff.
        """
        add_users(self.global_admin, CourseStaffRole(self.location), self.staff)
        self.assertEqual(
            'staff',
            get_user_role(self.staff, self.location, self.location.course_id)
        )

    def test_get_user_role_staff_locator(self):
        """
        Verifies if user is staff, using a CourseLocator.
        """
        add_users(self.global_admin, CourseStaffRole(self.locator), self.staff)
        self.assertEqual(
            'staff',
            get_user_role(self.staff, self.locator)
        )
