"""
Skill taxonomy + deterministic keyword extraction.

Shared by BOTH sides of the matching problem so their vocabulary is identical
by construction:
  • CV processing (app/workers/tasks.py) → user_skills
  • Job ingestion (app/services/scrape_service.py) → job_skills

Because both come from this one taxonomy, user↔job matching is exact
lowercased equality — no fuzzy matching needed. (Semantic/embedding matching
is the V2 story; see docs/roadmap/job-recommendations.md.)
"""


def extract_skills(text: str) -> list[tuple[str, str]]:
    """Extract (skill_name, category) for every taxonomy entry present in `text`.

    Case-insensitive substring match. Convenience wrapper over
    `extract_skills_from_lower` for callers holding raw text."""
    return extract_skills_from_lower((text or "").lower())


def extract_skills_from_lower(text_lower: str) -> list[tuple[str, str]]:
    """As `extract_skills`, for callers that already lowercased the text."""
    found = []
    for category, skills in SKILLS_TAXONOMY.items():
        for skill in skills:
            if skill.lower() in text_lower:
                found.append((skill, category))
    return found


# ── Skills taxonomy ───────────────────────────────────────────────────────────
# category → list of canonical skill names (matching is case-insensitive substring search)
SKILLS_TAXONOMY: dict[str, list[str]] = {
    "languages": [
        "Python", "JavaScript", "TypeScript", "Java", "Kotlin", "Swift",
        "Go", "Rust", "C++", "C#", "C", "Ruby", "PHP", "Scala", "R",
        "Dart", "Elixir", "Haskell", "Clojure", "Lua", "Perl", "MATLAB",
        "Bash", "PowerShell", "SQL", "HTML", "CSS", "Sass", "SCSS",
    ],
    "frontend": [
        "React", "Vue", "Angular", "Next.js", "Nuxt", "Svelte", "SvelteKit",
        "Redux", "MobX", "Zustand", "Tailwind CSS", "Bootstrap", "Material UI",
        "Chakra UI", "Ant Design", "Storybook", "Webpack", "Vite", "Rollup",
        "Babel", "ESLint", "Prettier", "Jest", "Cypress", "Playwright",
        "Three.js", "D3.js", "Chart.js", "WebGL", "WebRTC", "WebSockets",
        "PWA", "Service Workers", "Web Components",
    ],
    "backend": [
        "FastAPI", "Django", "Flask", "Express", "NestJS", "Spring Boot",
        "Laravel", "Rails", "Gin", "Echo", "Fiber", "ASP.NET Core",
        "GraphQL", "REST", "gRPC", "OAuth2", "JWT",
        "OpenAPI", "Swagger", "Celery", "RabbitMQ", "Kafka", "SQS",
        "Bull", "BullMQ", "Dramatiq", "Pydantic", "SQLAlchemy", "Prisma",
        "TypeORM", "Sequelize", "Mongoose", "Hibernate", "GORM",
    ],
    "databases": [
        "PostgreSQL", "MySQL", "SQLite", "MariaDB", "Oracle",
        "MongoDB", "Redis", "Cassandra", "DynamoDB", "CosmosDB",
        "Elasticsearch", "OpenSearch", "InfluxDB", "TimescaleDB",
        "Neo4j", "Dgraph", "Fauna", "Supabase", "PlanetScale",
        "pgvector", "Pinecone", "Qdrant", "Weaviate", "Chroma", "Milvus",
        "Firestore",
    ],
    "cloud": [
        "AWS", "GCP", "Azure", "Cloudflare", "DigitalOcean", "Heroku",
        "Vercel", "Netlify", "Railway", "Render",
        "S3", "EC2", "Lambda", "ECS", "EKS", "Fargate", "CloudFront",
        "RDS", "Aurora", "SageMaker", "Bedrock",
        "Cloud Run", "Cloud Functions", "BigQuery",
        "App Service", "Azure Functions",
    ],
    "devops": [
        "Docker", "Kubernetes", "Helm", "Terraform", "Ansible", "Pulumi",
        "GitHub Actions", "GitLab CI", "CircleCI", "Jenkins", "ArgoCD",
        "Prometheus", "Grafana", "Datadog", "New Relic", "Sentry",
        "ELK Stack", "Kibana", "Logstash", "Fluentd", "OpenTelemetry",
        "Nginx", "Traefik", "Istio", "Envoy", "Linkerd",
        "Linux",
    ],
    "mobile": [
        "Flutter", "React Native", "SwiftUI",
        "Jetpack Compose", "Xamarin", "Ionic", "Capacitor",
        "Android SDK", "iOS SDK", "Expo", "Firebase",
    ],
    "ai_ml": [
        "Machine Learning", "Deep Learning", "NLP", "Computer Vision",
        "PyTorch", "TensorFlow", "Keras", "Scikit-learn", "XGBoost",
        "LangChain", "LlamaIndex", "OpenAI", "Anthropic", "Hugging Face",
        "Transformers", "BERT", "GPT", "RAG", "Vector Search", "Embeddings",
        "pandas", "NumPy", "Matplotlib", "Seaborn", "Plotly",
        "Jupyter", "MLflow",
    ],
    "testing": [
        "Unit Testing", "Integration Testing", "End-to-End Testing",
        "TDD", "BDD", "Pytest", "Jest", "Mocha", "Chai",
        "Playwright", "Cypress", "Selenium", "Postman",
        "k6", "Locust", "JMeter",
    ],
    "soft_skills": [
        "Agile", "Scrum", "Kanban", "JIRA", "Confluence",
        "Technical Writing", "Code Review", "Pair Programming",
        "Mentoring", "System Design",
    ],
}
