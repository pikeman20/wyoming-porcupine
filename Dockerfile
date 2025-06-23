# syntax=docker/dockerfile:1

FROM python:3.11-slim

WORKDIR /app

COPY . /app

RUN pip install --upgrade pip \
    && pip install .

EXPOSE 11500

ENTRYPOINT ["python", "-m", "wyoming_porcupine"]
CMD []