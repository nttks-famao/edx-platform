"""
Course Import page.
"""

from .course_page import CoursePage


class ImportPage(CoursePage):
    """
    Course Import page.
    """

    url_path = "import"

    def is_browser_on_page(self):
        return self.q(css='body.view-import').present
