FROM python:3.12-alpine
ENV PASSPRASE=''
VOLUME ["/data"]
WORKDIR /usr/src/app
RUN apk add --no-cache gpg tar gzip docker-cli docker-cli-compose
COPY dkp ./

ENTRYPOINT [ "python3", "-u", "/usr/src/app/dkp.py" ]