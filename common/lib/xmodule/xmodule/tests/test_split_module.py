"""
Tests for the Split Testing Module
"""
import ddt
import lxml
from mock import Mock, patch
from fs.memoryfs import MemoryFS

from xmodule.tests.xml import factories as xml
from xmodule.tests.xml import XModuleXmlImportTest
from xmodule.tests import get_test_system
from xmodule.split_test_module import SplitTestDescriptor
from xmodule.partitions.partitions import Group, UserPartition
from xmodule.partitions.test_partitions import StaticPartitionService, MemoryUserTagsService


class SplitTestModuleFactory(xml.XmlImportFactory):
    """
    Factory for generating SplitTestModules for testing purposes
    """
    tag = 'split_test'


@ddt.ddt
class SplitTestModuleTest(XModuleXmlImportTest):
    """
    Test the split test module
    """

    def setUp(self):
        self.course_id = 'test_org/test_course_number/test_run'
        # construct module
        course = xml.CourseFactory.build()
        sequence = xml.SequenceFactory.build(parent=course)
        split_test = SplitTestModuleFactory(
            parent=sequence,
            attribs={
                'user_partition_id': '0',
                'group_id_to_child': '{"0": "i4x://edX/xml_test_course/html/split_test_cond0", "1": "i4x://edX/xml_test_course/html/split_test_cond1"}'
            }
        )
        xml.HtmlFactory(parent=split_test, url_name='split_test_cond0', text='HTML FOR GROUP 0')
        xml.HtmlFactory(parent=split_test, url_name='split_test_cond1', text='HTML FOR GROUP 1')

        self.course = self.process_xml(course)
        course_seq = self.course.get_children()[0]
        self.module_system = get_test_system()

        def get_module(descriptor):
            """Mocks module_system get_module function"""
            module_system = get_test_system()
            module_system.get_module = get_module
            descriptor.bind_for_student(module_system, descriptor._field_data)  # pylint: disable=protected-access
            return descriptor

        self.module_system.get_module = get_module
        self.module_system.descriptor_system = self.course.runtime
        self.course.runtime.export_fs = MemoryFS()

        self.tags_service = MemoryUserTagsService()
        self.module_system._services['user_tags'] = self.tags_service  # pylint: disable=protected-access

        self.partitions_service = StaticPartitionService(
            [
                UserPartition(0, 'first_partition', 'First Partition', [Group("0", 'alpha'), Group("1", 'beta')]),
                UserPartition(1, 'second_partition', 'Second Partition', [Group("0", 'abel'), Group("1", 'baker'), Group("2", 'charlie')])
            ],
            user_tags_service=self.tags_service,
            course_id=self.course.id,
            track_function=Mock(name='track_function'),
        )
        self.module_system._services['partitions'] = self.partitions_service  # pylint: disable=protected-access

        self.split_test_module = course_seq.get_children()[0]
        self.split_test_module.bind_for_student(self.module_system, self.split_test_module._field_data)  # pylint: disable=protected-access

    @ddt.data(('0', 'split_test_cond0'), ('1', 'split_test_cond1'))
    @ddt.unpack
    def test_child(self, user_tag, child_url_name):
        self.tags_service.set_tag(
            self.tags_service.COURSE_SCOPE,
            'xblock.partition_service.partition_0',
            user_tag
        )

        self.assertEquals(self.split_test_module.child_descriptor.url_name, child_url_name)

    @ddt.data(('0',), ('1',))
    @ddt.unpack
    def test_child_old_tag_value(self, _user_tag):
        # If user_tag has a stale value, we should still get back a valid child url
        self.tags_service.set_tag(
            self.tags_service.COURSE_SCOPE,
            'xblock.partition_service.partition_0',
            '2'
        )

        self.assertIn(self.split_test_module.child_descriptor.url_name, ['split_test_cond0', 'split_test_cond1'])

    @ddt.data(('0', 'HTML FOR GROUP 0'), ('1', 'HTML FOR GROUP 1'))
    @ddt.unpack
    def test_get_html(self, user_tag, child_content):
        self.tags_service.set_tag(
            self.tags_service.COURSE_SCOPE,
            'xblock.partition_service.partition_0',
            user_tag
        )

        self.assertIn(
            child_content,
            self.module_system.render(self.split_test_module, 'student_view').content
        )

    @ddt.data(('0',), ('1',))
    @ddt.unpack
    def test_child_missing_tag_value(self, _user_tag):
        # If user_tag has a missing value, we should still get back a valid child url
        self.assertIn(self.split_test_module.child_descriptor.url_name, ['split_test_cond0', 'split_test_cond1'])

    @ddt.data(('100',), ('200',), ('300',), ('400',), ('500',), ('600',), ('700',), ('800',), ('900',), ('1000',))
    @ddt.unpack
    def test_child_persist_new_tag_value_when_tag_missing(self, _user_tag):
        # If a user_tag has a missing value, a group should be saved/persisted for that user.
        # So, we check that we get the same url_name when we call on the url_name twice.
        # We run the test ten times so that, if our storage is failing, we'll be most likely to notice it.
        self.assertEquals(self.split_test_module.child_descriptor.url_name, self.split_test_module.child_descriptor.url_name)

    # Patch the definition_to_xml for the html children.
    @patch('xmodule.html_module.HtmlDescriptor.definition_to_xml')
    def test_export_import_round_trip(self, def_to_xml):
        # The HtmlDescriptor definition_to_xml tries to write to the filesystem
        # before returning an xml object. Patch this to just return the xml.
        def_to_xml.return_value = lxml.etree.Element('html')

        # Mock out the process_xml
        # Expect it to return a child descriptor for the SplitTestDescriptor when called.
        self.module_system.process_xml = Mock()

        # Write out the xml.
        xml_obj = self.split_test_module.definition_to_xml(MemoryFS())

        self.assertEquals(xml_obj.get('user_partition_id'), '0')
        self.assertIsNotNone(xml_obj.get('group_id_to_child'))

        # Read the xml back in.
        fields, children = SplitTestDescriptor.definition_from_xml(xml_obj, self.module_system)
        self.assertEquals(fields.get('user_partition_id'), '0')
        self.assertIsNotNone(fields.get('group_id_to_child'))
        self.assertEquals(len(children), 2)
