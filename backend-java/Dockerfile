# Imagen de bank-api-stress. Se construye a partir del jar ya compilado:
#   mvn clean package -DskipTests && docker build -t bank-api-stress .
# (docker compose up --build lo hace solo, pero el jar tiene que existir).
FROM eclipse-temurin:21-jre

WORKDIR /app
COPY target/bank-api-stress.jar app.jar

# El log de tráfico va a un volumen para poder leerlo desde fuera del contenedor
ENV TRAFFIC_LOG=/logs/trafico.json
VOLUME /logs

EXPOSE 7070
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
