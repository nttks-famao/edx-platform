#!/bin/sh 
sass -E utf-8 lms/static/sass/gacco/foundation.scss lms/static/sass/gacco/gacco.css --trace
python manage.py lms collectstatic --settings=aws
