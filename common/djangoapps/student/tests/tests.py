"""
This file demonstrates writing tests using the unittest module. These will pass
when you run "manage.py test".

Replace this with more appropriate tests for your application.
"""
import logging
import unittest
from datetime import datetime, timedelta
import pytz

from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from django.test.client import RequestFactory, Client
from django.contrib.auth.models import User, AnonymousUser
from django.contrib.auth.hashers import UNUSABLE_PASSWORD
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import int_to_base36
from django.core.urlresolvers import reverse, NoReverseMatch
from django.http import HttpResponse, Http404
from unittest.case import SkipTest

from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from courseware.tests.tests import TEST_DATA_MIXED_MODULESTORE

from mock import Mock, patch

from student.models import (anonymous_id_for_user, user_by_anonymous_id, CourseEnrollment, unique_id_for_user,
                            UserStanding)
from student.views import (process_survey_link, _cert_info,
                           change_enrollment, complete_course_mode_info, token,
                           resign, resign_confirm)
from student.tests.factories import UserFactory, UserStandingFactory, CourseModeFactory
from student.tests.test_email import mock_render_to_string

import shoppingcart

COURSE_1 = 'edX/toy/2012_Fall'
COURSE_2 = 'edx/full/6.002_Spring_2012'

log = logging.getLogger(__name__)


class ResignTests(TestCase):
    """
    Tests for resignation functionality
    """
    request_factory = RequestFactory()

    def setUp(self):
        self.user = UserFactory.create()
        self.user.is_active = False
        self.user.save()
        self.token = default_token_generator.make_token(self.user)
        self.uidb36 = int_to_base36(self.user.id)
        self.resign_reason = 'a' * 1000

    def test_resign_404(self):
        """Ensures that no get request to /resign/ is allowed"""

        bad_req = self.request_factory.get('/resign/')
        self.assertRaises(Http404, resign, bad_req)

    def test_resign_by_nonexist_email_user(self):
        """Now test the exception cases with of resign called with invalid email."""

        bad_email_req = self.request_factory.post('/resign/', {'email': self.user.email + "makeItFail"})
        bad_email_resp = resign(bad_email_req)
        # Note: even if the email is bad, we return a successful response code
        # This prevents someone potentially trying to "brute-force" find out which emails are and aren't registered with edX
        self.assertEquals(bad_email_resp.status_code, 200)
        obj = json.loads(bad_email_resp.content)
        self.assertEquals(obj, {
            'success': True,
        })

    def test_resign_ratelimited(self):
        """ Try (and fail) resigning 30 times in a row on an non-existant email address """
        cache.clear()

        for i in xrange(30):
            good_req = self.request_factory.post('/resign/', {'email': 'thisdoesnotexist@foo.com'})
            good_resp = resign(good_req)
            self.assertEquals(good_resp.status_code, 200)

        # then the rate limiter should kick in and give a HttpForbidden response
        bad_req = self.request_factory.post('/resign/', {'email': 'thisdoesnotexist@foo.com'})
        bad_resp = resign(bad_req)
        self.assertEquals(bad_resp.status_code, 403)

        cache.clear()

    @unittest.skipIf(
        settings.FEATURES.get('DISABLE_RESIGN_EMAIL_TEST', False),
        dedent("""
            Skipping Test because CMS has not provided necessary templates for resignation.
            If LMS tests print this message, that needs to be fixed.
        """)
    )
    @patch('django.core.mail.send_mail')
    @patch('student.views.render_to_string', Mock(side_effect=mock_render_to_string, autospec=True))
    def test_resign_email(self, send_email):
        """Tests contents of resign email"""

        good_req = self.request_factory.post('/resign/', {'email': self.user.email})
        good_resp = resign(good_req)
        self.assertEquals(good_resp.status_code, 200)
        obj = json.loads(good_resp.content)
        self.assertEquals(obj, {
            'success': True,
        })

        ((subject, msg, from_addr, to_addrs), sm_kwargs) = send_email.call_args
        self.assertIn("Resignation from", subject)
        self.assertIn("You're receiving this e-mail because you requested a resignation", msg)
        self.assertEquals(from_addr, settings.DEFAULT_FROM_EMAIL)
        self.assertEquals(len(to_addrs), 1)
        self.assertIn(self.user.email, to_addrs)

        # test that the user is not active (as well as test_reset_password_email)
        self.user = User.objects.get(pk=self.user.pk)
        self.assertFalse(self.user.is_active)
        url_match = re.search(r'resign_confirm/(?P<uidb36>[0-9A-Za-z]+)-(?P<token>.+)/', msg).groupdict()
        self.assertEquals(url_match['uidb36'], self.uidb36)
        self.assertEquals(url_match['token'], self.token)

    def test_resign_confirm_with_bad_token(self):
        """Ensures that get request with bad token and uidb36 to /resign_confirm/ is considered invalid link
        """
        bad_req = self.request_factory.get('/resign_confirm/NO-OP/')
        bad_resp = resign_confirm(bad_req, 'NO', 'OP')
        self.assertEquals(bad_resp.status_code, 200)
        self.assertEquals(bad_resp.template_name, 'registration/resign_confirm.html')
        self.assertIsNone(bad_resp.context_data['form'])
        self.assertFalse(bad_resp.context_data['validlink'])

    def test_resign_confirm_with_good_token(self):
        """Ensures that get request with good token and uidb36 to /resign_confirm/ is considered valid link
        """
        good_req = self.request_factory.get('/resign_confirm/{0}-{1}/'.format(self.uidb36, self.token))
        good_resp = resign_confirm(good_req, self.uidb36, self.token)
        self.assertEquals(good_resp.status_code, 200)
        self.assertEquals(good_resp.template_name, 'registration/resign_confirm.html')
        self.assertIsNotNone(good_resp.context_data['form'])
        self.assertTrue(good_resp.context_data['validlink'])

        # assert that the user's UserStanding record is not created yet
        self.assertRaises(
            UserStanding.DoesNotExist,
            UserStanding.objects.get,
            user=self.user)

    @patch('student.views.logout_user')
    def test_resign_confirm_with_good_reason(self, logout_user):
        """Ensures that post request with good resign_reason to /resign_confirm/ makes the user logged out and disabled
        """
        good_req = self.request_factory.post('/resign_confirm/{0}-{1}/'.format(self.uidb36, self.token),
                                             {'resign_reason': self.resign_reason})
        good_resp = resign_confirm(good_req, self.uidb36, self.token)
        self.assertTrue(logout_user.called)

        self.assertEquals(good_resp.status_code, 200)
        self.assertEquals(good_resp.template_name, 'registration/resign_complete.html')
        # assert that the user is active
        self.user = User.objects.get(pk=self.user.pk)
        self.assertTrue(self.user.is_active)
        # assert that the user's account_status is disabled
        user_account = UserStanding.objects.get(user=self.user)
        self.assertTrue(user_account.account_status, UserStanding.ACCOUNT_DISABLED)
        self.assertTrue(user_account.resign_reason, self.resign_reason)

    def test_resign_confirm_with_empty_reason(self):
        """Ensures that post request with empty resign_reason to /resign_confirm/ is considered invalid form
        """
        bad_req = self.request_factory.post(
            '/resign_confirm/{0}-{1}/'.format(self.uidb36, self.token),
            {'resign_reason': ''}
        )
        bad_resp = resign_confirm(bad_req, self.uidb36, self.token)

        self.assertEquals(bad_resp.status_code, 200)
        self.assertEquals(bad_resp.template_name, 'registration/resign_confirm.html')
        self.assertIsNotNone(bad_resp.context_data['form'])
        # assert that the returned form is invalid
        self.assertFalse(bad_resp.context_data['form'].is_valid())

    def test_resign_confirm_with_over_maxlength_reason(self):
        """Ensures that post request with over maxlength resign_reason to /resign_confirm/ is considered invalid form
        """
        bad_req = self.request_factory.post(
            '/resign_confirm/{0}-{1}/'.format(self.uidb36, self.token),
            {'resign_reason': self.resign_reason + 'a'}
        )
        bad_resp = resign_confirm(bad_req, self.uidb36, self.token)

        self.assertEquals(bad_resp.status_code, 200)
        self.assertEquals(bad_resp.template_name, 'registration/resign_confirm.html')
        self.assertIsNotNone(bad_resp.context_data['form'])
        # assert that the returned form is invalid
        self.assertFalse(bad_resp.context_data['form'].is_valid())


class ResignTests(TestCase):
    """
    Tests for resignation functionality
    """
    request_factory = RequestFactory()

    def setUp(self):
        self.user = UserFactory.create()
        self.user.is_active = False
        self.user.save()
        self.token = default_token_generator.make_token(self.user)
        self.uidb36 = int_to_base36(self.user.id)
        self.resign_reason = 'a' * 1000

    def test_resign_404(self):
        """Ensures that no get request to /resign/ is allowed"""

        bad_req = self.request_factory.get('/resign/')
        self.assertRaises(Http404, resign, bad_req)

    def test_resign_by_nonexist_email_user(self):
        """Now test the exception cases with of resign called with invalid email."""

        bad_email_req = self.request_factory.post('/resign/', {'email': self.user.email + "makeItFail"})
        bad_email_resp = resign(bad_email_req)
        # Note: even if the email is bad, we return a successful response code
        # This prevents someone potentially trying to "brute-force" find out which emails are and aren't registered with edX
        self.assertEquals(bad_email_resp.status_code, 200)
        obj = json.loads(bad_email_resp.content)
        self.assertEquals(obj, {
            'success': True,
        })

    @unittest.skipIf(
        settings.FEATURES.get('DISABLE_RESIGN_EMAIL_TEST', False),
        dedent("""
            Skipping Test because CMS has not provided necessary templates for resignation.
            If LMS tests print this message, that needs to be fixed.
        """)
    )
    @patch('django.core.mail.send_mail')
    @patch('student.views.render_to_string', Mock(side_effect=mock_render_to_string, autospec=True))
    def test_resign_email(self, send_email):
        """Tests contents of resign email"""

        good_req = self.request_factory.post('/resign/', {'email': self.user.email})
        good_resp = resign(good_req)
        self.assertEquals(good_resp.status_code, 200)
        obj = json.loads(good_resp.content)
        self.assertEquals(obj, {
            'success': True,
        })

        ((subject, msg, from_addr, to_addrs), sm_kwargs) = send_email.call_args
        self.assertIn("Resignation from", subject)
        self.assertIn("You're receiving this e-mail because you requested a resignation", msg)
        self.assertEquals(from_addr, settings.DEFAULT_FROM_EMAIL)
        self.assertEquals(len(to_addrs), 1)
        self.assertIn(self.user.email, to_addrs)

        # test that the user is not active (as well as test_reset_password_email)
        self.user = User.objects.get(pk=self.user.pk)
        self.assertFalse(self.user.is_active)
        url_match = re.search(r'resign_confirm/(?P<uidb36>[0-9A-Za-z]+)-(?P<token>.+)/', msg).groupdict()
        self.assertEquals(url_match['uidb36'], self.uidb36)
        self.assertEquals(url_match['token'], self.token)

    def test_resign_confirm_with_bad_token(self):
        """Ensures that get request with bad token and uidb36 to /resign_confirm/ is considered invalid link
        """
        bad_req = self.request_factory.get('/resign_confirm/NO-OP/')
        bad_resp = resign_confirm(bad_req, 'NO', 'OP')
        self.assertEquals(bad_resp.status_code, 200)
        self.assertEquals(bad_resp.template_name, 'registration/resign_confirm.html')
        self.assertIsNone(bad_resp.context_data['form'])
        self.assertFalse(bad_resp.context_data['validlink'])

    def test_resign_confirm_with_good_token(self):
        """Ensures that get request with good token and uidb36 to /resign_confirm/ is considered valid link
        """
        good_req = self.request_factory.get('/resign_confirm/{0}-{1}/'.format(self.uidb36, self.token))
        good_resp = resign_confirm(good_req, self.uidb36, self.token)
        self.assertEquals(good_resp.status_code, 200)
        self.assertEquals(good_resp.template_name, 'registration/resign_confirm.html')
        self.assertIsNotNone(good_resp.context_data['form'])
        self.assertTrue(good_resp.context_data['validlink'])

        # assert that the user's UserStanding record is not created yet
        self.assertRaises(
            UserStanding.DoesNotExist,
            UserStanding.objects.get,
            user=self.user)

    @patch('student.views.logout_user')
    def test_resign_confirm_with_good_reason(self, logout_user):
        """Ensures that post request with good resign_reason to /resign_confirm/ makes the user logged out and disabled
        """
        good_req = self.request_factory.post('/resign_confirm/{0}-{1}/'.format(self.uidb36, self.token),
                                             {'resign_reason': self.resign_reason})
        good_resp = resign_confirm(good_req, self.uidb36, self.token)
        self.assertTrue(logout_user.called)

        self.assertEquals(good_resp.status_code, 200)
        self.assertEquals(good_resp.template_name, 'registration/resign_complete.html')
        # assert that the user is active
        self.user = User.objects.get(pk=self.user.pk)
        self.assertTrue(self.user.is_active)
        # assert that the user's account_status is disabled
        user_account = UserStanding.objects.get(user=self.user)
        self.assertTrue(user_account.account_status, UserStanding.ACCOUNT_DISABLED)
        self.assertTrue(user_account.resign_reason, self.resign_reason)

    def test_resign_confirm_with_empty_reason(self):
        """Ensures that post request with empty resign_reason to /resign_confirm/ is considered invalid form
        """
        bad_req = self.request_factory.post(
            '/resign_confirm/{0}-{1}/'.format(self.uidb36, self.token),
            {'resign_reason': ''}
        )
        bad_resp = resign_confirm(bad_req, self.uidb36, self.token)

        self.assertEquals(bad_resp.status_code, 200)
        self.assertEquals(bad_resp.template_name, 'registration/resign_confirm.html')
        self.assertIsNotNone(bad_resp.context_data['form'])
        # assert that the returned form is invalid
        self.assertFalse(bad_resp.context_data['form'].is_valid())

    def test_resign_confirm_with_over_maxlength_reason(self):
        """Ensures that post request with over maxlength resign_reason to /resign_confirm/ is considered invalid form
        """
        bad_req = self.request_factory.post(
            '/resign_confirm/{0}-{1}/'.format(self.uidb36, self.token),
            {'resign_reason': self.resign_reason + 'a'}
        )
        bad_resp = resign_confirm(bad_req, self.uidb36, self.token)

        self.assertEquals(bad_resp.status_code, 200)
        self.assertEquals(bad_resp.template_name, 'registration/resign_confirm.html')
        self.assertIsNotNone(bad_resp.context_data['form'])
        # assert that the returned form is invalid
        self.assertFalse(bad_resp.context_data['form'].is_valid())


class CourseEndingTest(TestCase):
    """Test things related to course endings: certificates, surveys, etc"""

    def test_process_survey_link(self):
        username = "fred"
        user = Mock(username=username)
        id = unique_id_for_user(user)
        link1 = "http://www.mysurvey.com"
        self.assertEqual(process_survey_link(link1, user), link1)

        link2 = "http://www.mysurvey.com?unique={UNIQUE_ID}"
        link2_expected = "http://www.mysurvey.com?unique={UNIQUE_ID}".format(UNIQUE_ID=id)
        self.assertEqual(process_survey_link(link2, user), link2_expected)

    def test_cert_info(self):
        user = Mock(username="fred")
        survey_url = "http://a_survey.com"
        course = Mock(end_of_course_survey_url=survey_url)

        self.assertEqual(_cert_info(user, course, None),
                         {'status': 'processing',
                          'show_disabled_download_button': False,
                          'show_download_url': False,
                          'show_survey_button': False,
                          })

        cert_status = {'status': 'unavailable'}
        self.assertEqual(_cert_info(user, course, cert_status),
                         {'status': 'processing',
                          'show_disabled_download_button': False,
                          'show_download_url': False,
                          'show_survey_button': False,
                          'mode': None
                          })

        cert_status = {'status': 'generating', 'grade': '67', 'mode': 'honor'}
        self.assertEqual(_cert_info(user, course, cert_status),
                         {'status': 'generating',
                          'show_disabled_download_button': True,
                          'show_download_url': False,
                          'show_survey_button': True,
                          'survey_url': survey_url,
                          'grade': '67',
                          'mode': 'honor'
                          })

        cert_status = {'status': 'regenerating', 'grade': '67', 'mode': 'verified'}
        self.assertEqual(_cert_info(user, course, cert_status),
                         {'status': 'generating',
                          'show_disabled_download_button': True,
                          'show_download_url': False,
                          'show_survey_button': True,
                          'survey_url': survey_url,
                          'grade': '67',
                          'mode': 'verified'
                          })

        download_url = 'http://s3.edx/cert'
        cert_status = {'status': 'downloadable', 'grade': '67',
                       'download_url': download_url, 'mode': 'honor'}
        self.assertEqual(_cert_info(user, course, cert_status),
                         {'status': 'ready',
                          'show_disabled_download_button': False,
                          'show_download_url': True,
                          'download_url': download_url,
                          'show_survey_button': True,
                          'survey_url': survey_url,
                          'grade': '67',
                          'mode': 'honor'
                          })

        cert_status = {'status': 'notpassing', 'grade': '67',
                       'download_url': download_url, 'mode': 'honor'}
        self.assertEqual(_cert_info(user, course, cert_status),
                         {'status': 'notpassing',
                          'show_disabled_download_button': False,
                          'show_download_url': False,
                          'show_survey_button': True,
                          'survey_url': survey_url,
                          'grade': '67',
                          'mode': 'honor'
                          })

        # Test a course that doesn't have a survey specified
        course2 = Mock(end_of_course_survey_url=None)
        cert_status = {'status': 'notpassing', 'grade': '67',
                       'download_url': download_url, 'mode': 'honor'}
        self.assertEqual(_cert_info(user, course2, cert_status),
                         {'status': 'notpassing',
                          'show_disabled_download_button': False,
                          'show_download_url': False,
                          'show_survey_button': False,
                          'grade': '67',
                          'mode': 'honor'
                          })


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class DashboardTest(TestCase):
    """
    Tests for dashboard utility functions
    """
    # arbitrary constant
    COURSE_SLUG = "100"
    COURSE_NAME = "test_course"
    COURSE_ORG = "EDX"

    def setUp(self):
        self.course = CourseFactory.create(org=self.COURSE_ORG, display_name=self.COURSE_NAME, number=self.COURSE_SLUG)
        self.assertIsNotNone(self.course)
        self.user = UserFactory.create(username="jack", email="jack@fake.edx.org", password='test')
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='honor',
            mode_display_name='Honor Code',
        )
        self.client = Client()

    def check_verification_status_on(self, mode, value):
        """
        Check that the css class and the status message are in the dashboard html.
        """
        CourseEnrollment.enroll(self.user, self.course.location.course_id, mode=mode)
        try:
            response = self.client.get(reverse('dashboard'))
        except NoReverseMatch:
            raise SkipTest("Skip this test if url cannot be found (ie running from CMS tests)")
        self.assertContains(response, "class=\"course {0}\"".format(mode))
        self.assertContains(response, value)

    @patch.dict("django.conf.settings.FEATURES", {'ENABLE_VERIFIED_CERTIFICATES': True})
    def test_verification_status_visible(self):
        """
        Test that the certificate verification status for courses is visible on the dashboard.
        """
        self.client.login(username="jack", password="test")
        self.check_verification_status_on('verified', 'You\'re enrolled as a verified student')
        self.check_verification_status_on('honor', 'You\'re enrolled as an honor code student')
        self.check_verification_status_on('audit', 'You\'re auditing this course')

    def check_verification_status_off(self, mode, value):
        """
        Check that the css class and the status message are not in the dashboard html.
        """
        CourseEnrollment.enroll(self.user, self.course.location.course_id, mode=mode)
        try:
            response = self.client.get(reverse('dashboard'))
        except NoReverseMatch:
            raise SkipTest("Skip this test if url cannot be found (ie running from CMS tests)")
        self.assertNotContains(response, "class=\"course {0}\"".format(mode))
        self.assertNotContains(response, value)

    @patch.dict("django.conf.settings.FEATURES", {'ENABLE_VERIFIED_CERTIFICATES': False})
    def test_verification_status_invisible(self):
        """
        Test that the certificate verification status for courses is not visible on the dashboard
        if the verified certificates setting is off.
        """
        self.client.login(username="jack", password="test")
        self.check_verification_status_off('verified', 'You\'re enrolled as a verified student')
        self.check_verification_status_off('honor', 'You\'re enrolled as an honor code student')
        self.check_verification_status_off('audit', 'You\'re auditing this course')

    def test_course_mode_info(self):
        verified_mode = CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='verified',
            mode_display_name='Verified',
            expiration_datetime=datetime.now(pytz.UTC) + timedelta(days=1)
        )
        enrollment = CourseEnrollment.enroll(self.user, self.course.id)
        course_mode_info = complete_course_mode_info(self.course.id, enrollment)
        self.assertTrue(course_mode_info['show_upsell'])
        self.assertEquals(course_mode_info['days_for_upsell'], 1)

        verified_mode.expiration_datetime = datetime.now(pytz.UTC) + timedelta(days=-1)
        verified_mode.save()
        course_mode_info = complete_course_mode_info(self.course.id, enrollment)
        self.assertFalse(course_mode_info['show_upsell'])
        self.assertIsNone(course_mode_info['days_for_upsell'])

    def test_refundable(self):
        verified_mode = CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='verified',
            mode_display_name='Verified',
            expiration_datetime=datetime.now(pytz.UTC) + timedelta(days=1)
        )
        enrollment = CourseEnrollment.enroll(self.user, self.course.id, mode='verified')

        self.assertTrue(enrollment.refundable())

        verified_mode.expiration_datetime = datetime.now(pytz.UTC) - timedelta(days=1)
        verified_mode.save()
        self.assertFalse(enrollment.refundable())



class EnrollInCourseTest(TestCase):
    """Tests enrolling and unenrolling in courses."""

    def setUp(self):
        patcher = patch('student.models.tracker')
        self.mock_tracker = patcher.start()
        self.addCleanup(patcher.stop)

    def test_enrollment(self):
        user = User.objects.create_user("joe", "joe@joe.com", "password")
        course_id = "edX/Test101/2013"
        course_id_partial = "edX/Test101"

        # Test basic enrollment
        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id))
        self.assertFalse(CourseEnrollment.is_enrolled_by_partial(user,
            course_id_partial))
        CourseEnrollment.enroll(user, course_id)
        self.assertTrue(CourseEnrollment.is_enrolled(user, course_id))
        self.assertTrue(CourseEnrollment.is_enrolled_by_partial(user,
            course_id_partial))
        self.assert_enrollment_event_was_emitted(user, course_id)

        # Enrolling them again should be harmless
        CourseEnrollment.enroll(user, course_id)
        self.assertTrue(CourseEnrollment.is_enrolled(user, course_id))
        self.assertTrue(CourseEnrollment.is_enrolled_by_partial(user,
            course_id_partial))
        self.assert_no_events_were_emitted()

        # Now unenroll the user
        CourseEnrollment.unenroll(user, course_id)
        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id))
        self.assertFalse(CourseEnrollment.is_enrolled_by_partial(user,
            course_id_partial))
        self.assert_unenrollment_event_was_emitted(user, course_id)

        # Unenrolling them again should also be harmless
        CourseEnrollment.unenroll(user, course_id)
        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id))
        self.assertFalse(CourseEnrollment.is_enrolled_by_partial(user,
            course_id_partial))
        self.assert_no_events_were_emitted()

        # The enrollment record should still exist, just be inactive
        enrollment_record = CourseEnrollment.objects.get(
            user=user,
            course_id=course_id
        )
        self.assertFalse(enrollment_record.is_active)

        # Make sure mode is updated properly if user unenrolls & re-enrolls
        enrollment = CourseEnrollment.enroll(user, course_id, "verified")
        self.assertEquals(enrollment.mode, "verified")
        CourseEnrollment.unenroll(user, course_id)
        enrollment = CourseEnrollment.enroll(user, course_id, "audit")
        self.assertTrue(CourseEnrollment.is_enrolled(user, course_id))
        self.assertEquals(enrollment.mode, "audit")

    def assert_no_events_were_emitted(self):
        """Ensures no events were emitted since the last event related assertion"""
        self.assertFalse(self.mock_tracker.emit.called)  # pylint: disable=maybe-no-member
        self.mock_tracker.reset_mock()

    def assert_enrollment_event_was_emitted(self, user, course_id):
        """Ensures an enrollment event was emitted since the last event related assertion"""
        self.mock_tracker.emit.assert_called_once_with(  # pylint: disable=maybe-no-member
            'edx.course.enrollment.activated',
            {
                'course_id': course_id,
                'user_id': user.pk,
                'mode': 'honor'
            }
        )
        self.mock_tracker.reset_mock()

    def assert_unenrollment_event_was_emitted(self, user, course_id):
        """Ensures an unenrollment event was emitted since the last event related assertion"""
        self.mock_tracker.emit.assert_called_once_with(  # pylint: disable=maybe-no-member
            'edx.course.enrollment.deactivated',
            {
                'course_id': course_id,
                'user_id': user.pk,
                'mode': 'honor'
            }
        )
        self.mock_tracker.reset_mock()

    def test_enrollment_non_existent_user(self):
        # Testing enrollment of newly unsaved user (i.e. no database entry)
        user = User(username="rusty", email="rusty@fake.edx.org")
        course_id = "edX/Test101/2013"

        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id))

        # Unenroll does nothing
        CourseEnrollment.unenroll(user, course_id)
        self.assert_no_events_were_emitted()

        # Implicit save() happens on new User object when enrolling, so this
        # should still work
        CourseEnrollment.enroll(user, course_id)
        self.assertTrue(CourseEnrollment.is_enrolled(user, course_id))
        self.assert_enrollment_event_was_emitted(user, course_id)

    def test_enrollment_by_email(self):
        user = User.objects.create(username="jack", email="jack@fake.edx.org")
        course_id = "edX/Test101/2013"

        CourseEnrollment.enroll_by_email("jack@fake.edx.org", course_id)
        self.assertTrue(CourseEnrollment.is_enrolled(user, course_id))
        self.assert_enrollment_event_was_emitted(user, course_id)

        # This won't throw an exception, even though the user is not found
        self.assertIsNone(
            CourseEnrollment.enroll_by_email("not_jack@fake.edx.org", course_id)
        )
        self.assert_no_events_were_emitted()

        self.assertRaises(
            User.DoesNotExist,
            CourseEnrollment.enroll_by_email,
            "not_jack@fake.edx.org",
            course_id,
            ignore_errors=False
        )
        self.assert_no_events_were_emitted()

        # Now unenroll them by email
        CourseEnrollment.unenroll_by_email("jack@fake.edx.org", course_id)
        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id))
        self.assert_unenrollment_event_was_emitted(user, course_id)

        # Harmless second unenroll
        CourseEnrollment.unenroll_by_email("jack@fake.edx.org", course_id)
        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id))
        self.assert_no_events_were_emitted()

        # Unenroll on non-existent user shouldn't throw an error
        CourseEnrollment.unenroll_by_email("not_jack@fake.edx.org", course_id)
        self.assert_no_events_were_emitted()

    def test_enrollment_multiple_classes(self):
        user = User(username="rusty", email="rusty@fake.edx.org")
        course_id1 = "edX/Test101/2013"
        course_id2 = "MITx/6.003z/2012"

        CourseEnrollment.enroll(user, course_id1)
        self.assert_enrollment_event_was_emitted(user, course_id1)
        CourseEnrollment.enroll(user, course_id2)
        self.assert_enrollment_event_was_emitted(user, course_id2)
        self.assertTrue(CourseEnrollment.is_enrolled(user, course_id1))
        self.assertTrue(CourseEnrollment.is_enrolled(user, course_id2))

        CourseEnrollment.unenroll(user, course_id1)
        self.assert_unenrollment_event_was_emitted(user, course_id1)
        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id1))
        self.assertTrue(CourseEnrollment.is_enrolled(user, course_id2))

        CourseEnrollment.unenroll(user, course_id2)
        self.assert_unenrollment_event_was_emitted(user, course_id2)
        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id1))
        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id2))

    def test_activation(self):
        user = User.objects.create(username="jack", email="jack@fake.edx.org")
        course_id = "edX/Test101/2013"
        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id))

        # Creating an enrollment doesn't actually enroll a student
        # (calling CourseEnrollment.enroll() would have)
        enrollment = CourseEnrollment.get_or_create_enrollment(user, course_id)
        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id))
        self.assert_no_events_were_emitted()

        # Until you explicitly activate it
        enrollment.activate()
        self.assertTrue(CourseEnrollment.is_enrolled(user, course_id))
        self.assert_enrollment_event_was_emitted(user, course_id)

        # Activating something that's already active does nothing
        enrollment.activate()
        self.assertTrue(CourseEnrollment.is_enrolled(user, course_id))
        self.assert_no_events_were_emitted()

        # Now deactive
        enrollment.deactivate()
        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id))
        self.assert_unenrollment_event_was_emitted(user, course_id)

        # Deactivating something that's already inactive does nothing
        enrollment.deactivate()
        self.assertFalse(CourseEnrollment.is_enrolled(user, course_id))
        self.assert_no_events_were_emitted()

        # A deactivated enrollment should be activated if enroll() is called
        # for that user/course_id combination
        CourseEnrollment.enroll(user, course_id)
        self.assertTrue(CourseEnrollment.is_enrolled(user, course_id))
        self.assert_enrollment_event_was_emitted(user, course_id)


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class PaidRegistrationTest(ModuleStoreTestCase):
    """
    Tests for paid registration functionality (not verified student), involves shoppingcart
    """
    # arbitrary constant
    COURSE_SLUG = "100"
    COURSE_NAME = "test_course"
    COURSE_ORG = "EDX"

    def setUp(self):
        # Create course
        self.req_factory = RequestFactory()
        self.course = CourseFactory.create(org=self.COURSE_ORG, display_name=self.COURSE_NAME, number=self.COURSE_SLUG)
        self.assertIsNotNone(self.course)
        self.user = User.objects.create(username="jack", email="jack@fake.edx.org")

    @unittest.skipUnless(settings.FEATURES.get('ENABLE_SHOPPING_CART'), "Shopping Cart not enabled in settings")
    def test_change_enrollment_add_to_cart(self):
        request = self.req_factory.post(reverse('change_enrollment'), {'course_id': self.course.id,
                                                                       'enrollment_action': 'add_to_cart'})
        request.user = self.user
        response = change_enrollment(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, reverse('shoppingcart.views.show_cart'))
        self.assertTrue(shoppingcart.models.PaidCourseRegistration.contained_in_order(
            shoppingcart.models.Order.get_cart_for_user(self.user), self.course.id))


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class AnonymousLookupTable(TestCase):
    """
    Tests for anonymous_id_functions
    """
    # arbitrary constant
    COURSE_SLUG = "100"
    COURSE_NAME = "test_course"
    COURSE_ORG = "EDX"

    def setUp(self):
        self.course = CourseFactory.create(org=self.COURSE_ORG, display_name=self.COURSE_NAME, number=self.COURSE_SLUG)
        self.assertIsNotNone(self.course)
        self.user = UserFactory()
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='honor',
            mode_display_name='Honor Code',
        )
        patcher = patch('student.models.tracker')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_for_unregistered_user(self):  # same path as for logged out user
        self.assertEqual(None, anonymous_id_for_user(AnonymousUser(), self.course.id))
        self.assertIsNone(user_by_anonymous_id(None))

    def test_roundtrip_for_logged_user(self):
        enrollment = CourseEnrollment.enroll(self.user, self.course.id)
        anonymous_id = anonymous_id_for_user(self.user, self.course.id)
        real_user = user_by_anonymous_id(anonymous_id)
        self.assertEqual(self.user, real_user)


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class Token(ModuleStoreTestCase):
    """
    Test for the token generator. This creates a random course and passes it through the token file which generates the
    token that will be passed in to the annotation_storage_url.
    """
    request_factory = RequestFactory()
    COURSE_SLUG = "100"
    COURSE_NAME = "test_course"
    COURSE_ORG = "edx"

    def setUp(self):
        self.course = CourseFactory.create(org=self.COURSE_ORG, display_name=self.COURSE_NAME, number=self.COURSE_SLUG)
        self.user = User.objects.create(username="username", email="username")
        self.req = self.request_factory.post('/token?course_id=edx/100/test_course', {'user': self.user})
        self.req.user = self.user

    def test_token(self):
        expected = HttpResponse("eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3N1ZWRBdCI6ICIyMDE0LTAxLTIzVDE5OjM1OjE3LjUyMjEwNC01OjAwIiwgImNvbnN1bWVyS2V5IjogInh4eHh4eHh4LXh4eHgteHh4eC14eHh4LXh4eHh4eHh4eHh4eCIsICJ1c2VySWQiOiAidXNlcm5hbWUiLCAidHRsIjogODY0MDB9.OjWz9mzqJnYuzX-f3uCBllqJUa8PVWJjcDy_McfxLvc", mimetype="text/plain")
        response = token(self.req)
        self.assertEqual(expected.content.split('.')[0], response.content.split('.')[0])
