#!/usr/bin/env bash

# Run this from travis

# Print runtime variable
echo "Running GeoSAFE coverage test"
echo "COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME"
echo "COMPOSE_FILE=$COMPOSE_FILE"

until docker-compose exec django /bin/bash -c "export TESTING=True; export COVERAGE_PROCESS_START=/usr/src/geosafe/.coveragerc; coverage run --rcfile=/usr/src/geosafe/.coveragerc manage.py test geosafe --noinput --liveserver=0.0.0.0:8000 --nomigrations"; do
	# Retrieve exit code
	exit_code=$?
	echo "EXIT CODE: $exit_code"

	if [ "$exit_code" -eq "1" ]; then
		echo "Unittests failed"
		exit 1
	fi
	# investigate why it failed
	echo "Is it memory error?"
	journalctl -k | grep -i -e memory -e oom

	echo "Docker inspect"
	docker inspect geonode-docker_django_1

	# Restart attempt
	echo "$PWD"
	make up
	sleep 10
done

echo "Testing finished"
