FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache postgresql-client gcc musl-dev postgresql-dev linux-headers

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
