"""Django management command to force certificate generation"""
from optparse import make_option
from django.core.management.base import BaseCommand, CommandError
from pdfgen.certificate import CertificatePDF
from resource import setrlimit, RLIMIT_NOFILE


class Command(BaseCommand):
    args = "<course_id>"
    help = """Certificate command."""
    option_list = BaseCommand.option_list + (
        make_option('-n', '--noop',
            action='store_true',
            dest='noop',
            default=False,
            help="Print but do not anything."),
        make_option('-u', '--user',
            metavar='USERNAME',
            dest='username',
            default=False,
            help='The username or email address for whom grading'
                'and certification should be requested'),
        make_option('-d', '--debug',
            action="store_true",
            dest='debug',
            default=False,
            help='Debug print'),
        make_option('-m', '--method',
            metavar='method',
            dest='method',
            default='report',
            help='Create or delete or report'),
        make_option('-f', '--filedescriptors',
            type="int",
            metavar='fd',
            dest='fd',
            default=1024,
            help='Number of file descriptors')
    )

    def handle(self, *args, **options):
        user = options['username']
        debug = options['debug']
        noop = options['noop']
        method = options['method']
        fd = options['fd']

        if len(args) != 1:
            raise CommandError("course_id not specified.")

        if fd <= 0:
            raise CommandError("File descriptors is required to specify positive value.")

        setrlimit(RLIMIT_NOFILE, (fd, fd))
        course_id = args[0]
        certpdf = CertificatePDF(user, course_id, debug, noop)

        if method == "create":
            certpdf.create()
        elif method == "delete":
            certpdf.delete()
        elif method == "report":
            certpdf.report()
        else:
            raise CommandError('Invalid method.')
