-- Schema `venus` — derivado das queries em src/venus_sdk/tools/*.py.
-- Idempotente: pode ser reaplicado (DROP SCHEMA ... CASCADE antes, se quiser zerar).
CREATE SCHEMA IF NOT EXISTS venus;

CREATE TABLE IF NOT EXISTS venus.brands (
    brand_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS venus.product_categories (
    product_category_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS venus.products (
    product_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    slug TEXT UNIQUE,
    fk_brand_id INT NOT NULL REFERENCES venus.brands(brand_id),
    fk_product_category_id INT NOT NULL REFERENCES venus.product_categories(product_category_id)
);

CREATE TABLE IF NOT EXISTS venus.product_versions (
    product_version_id SERIAL PRIMARY KEY,
    fk_product_id INT NOT NULL REFERENCES venus.products(product_id),
    is_current BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS venus.product_scores (
    product_score_id SERIAL PRIMARY KEY,
    fk_product_version_id INT NOT NULL REFERENCES venus.product_versions(product_version_id),
    overall_score NUMERIC(5,2),
    health_score NUMERIC(5,2),
    environmental_score NUMERIC(5,2),
    ethical_score NUMERIC(5,2),
    performance_score NUMERIC(5,2),
    transparency_score NUMERIC(5,2),
    confidence_score NUMERIC(5,2)
);

CREATE TABLE IF NOT EXISTS venus.users (
    user_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    -- uid do Firebase Auth: é por ele que a API descobre o user_id de quem
    -- está no chat (nunca pelo que o app manda no corpo da requisição).
    firebase_uid TEXT UNIQUE
);
-- Bancos criados antes da coluna existir (no QA e em produção ela já existe).
-- TODO(app): o cadastro precisa gravar o firebase_uid de todo usuário novo;
-- sem ele, a Venus conversa normalmente, mas sem acesso aos dados da conta.
ALTER TABLE venus.users ADD COLUMN IF NOT EXISTS firebase_uid TEXT UNIQUE;

CREATE TABLE IF NOT EXISTS venus.personalized_scores (
    personalized_score_id SERIAL PRIMARY KEY,
    fk_product_version_id INT NOT NULL REFERENCES venus.product_versions(product_version_id),
    fk_user_id INT NOT NULL REFERENCES venus.users(user_id),
    final_score NUMERIC(5,2),
    compatibility_percentage NUMERIC(5,2),
    risk_level TEXT,
    recommendation_level TEXT,
    summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS venus.ingredients (
    ingredient_id SERIAL PRIMARY KEY,
    common_name TEXT NOT NULL,
    inci_name TEXT NOT NULL,
    function_summary TEXT,
    safety_summary TEXT,
    scientific_confidence TEXT,
    source_reference TEXT
);

CREATE TABLE IF NOT EXISTS venus.ingredient_aliases (
    ingredient_alias_id SERIAL PRIMARY KEY,
    fk_ingredient_id INT NOT NULL REFERENCES venus.ingredients(ingredient_id),
    alias_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS venus.product_ingredients (
    product_ingredient_id SERIAL PRIMARY KEY,
    fk_product_version_id INT NOT NULL REFERENCES venus.product_versions(product_version_id),
    fk_ingredient_id INT NOT NULL REFERENCES venus.ingredients(ingredient_id),
    position INT NOT NULL
);

CREATE TABLE IF NOT EXISTS venus.ingredient_properties (
    ingredient_property_id SERIAL PRIMARY KEY,
    fk_ingredient_id INT NOT NULL REFERENCES venus.ingredients(ingredient_id),
    property_name TEXT NOT NULL,
    property_value TEXT,
    unit TEXT,
    source_reference TEXT
);

CREATE TABLE IF NOT EXISTS venus.profile_tags (
    profile_tag_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS venus.ingredient_effects (
    ingredient_effect_id SERIAL PRIMARY KEY,
    fk_ingredient_id INT NOT NULL REFERENCES venus.ingredients(ingredient_id),
    fk_profile_tag_id INT NOT NULL REFERENCES venus.profile_tags(profile_tag_id),
    effect_category TEXT,
    effect_name TEXT,
    effect_description TEXT,
    effect_strength TEXT,
    evidence_level TEXT,
    source_reference TEXT
);

CREATE TABLE IF NOT EXISTS venus.regulations (
    regulation_id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    country TEXT,
    agency TEXT,
    document_url TEXT
);

CREATE TABLE IF NOT EXISTS venus.ingredient_regulations (
    ingredient_regulation_id SERIAL PRIMARY KEY,
    fk_ingredient_id INT NOT NULL REFERENCES venus.ingredients(ingredient_id),
    fk_regulation_id INT NOT NULL REFERENCES venus.regulations(regulation_id),
    restriction_type TEXT,
    max_concentration_value NUMERIC(8,3),
    unit TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS venus.allergies (
    allergy_id SERIAL PRIMARY KEY,
    allergy_name TEXT NOT NULL,
    allergy_type TEXT
);

CREATE TABLE IF NOT EXISTS venus.user_allergies (
    user_allergy_id SERIAL PRIMARY KEY,
    fk_user_id INT NOT NULL REFERENCES venus.users(user_id),
    fk_allergy_id INT NOT NULL REFERENCES venus.allergies(allergy_id),
    severity TEXT
);

-- --- Rotina (favoritos, listas e perfil do usuário) ---
CREATE TABLE IF NOT EXISTS venus.user_profiles (
    user_profile_id SERIAL PRIMARY KEY,
    fk_user_id INT NOT NULL UNIQUE REFERENCES venus.users(user_id),
    skin_type TEXT,
    hair_type TEXT,
    concerns TEXT
);

CREATE TABLE IF NOT EXISTS venus.favorites (
    favorite_id SERIAL PRIMARY KEY,
    fk_user_id INT NOT NULL REFERENCES venus.users(user_id),
    fk_product_id INT NOT NULL REFERENCES venus.products(product_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (fk_user_id, fk_product_id)
);

CREATE TABLE IF NOT EXISTS venus.user_lists (
    user_list_id SERIAL PRIMARY KEY,
    fk_user_id INT NOT NULL REFERENCES venus.users(user_id),
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS venus.user_list_items (
    user_list_item_id SERIAL PRIMARY KEY,
    fk_user_list_id INT NOT NULL REFERENCES venus.user_lists(user_list_id),
    fk_product_id INT NOT NULL REFERENCES venus.products(product_id)
);

-- --- Integração Google Calendar (ver venus_sdk/integrations/google_calendar.py) ---
-- Um refresh_token por usuário. Quem grava é o backend do mobile/web (o SDK
-- nunca inicia o fluxo OAuth), via salvar_refresh_token; o agente de rotina
-- só lê. Guardado CIFRADO (Fernet, GOOGLE_TOKEN_ENCRYPTION_KEY) — nunca em
-- texto puro. Nomes no padrão do schema `venus` (inglês, created_at/updated_at);
-- no banco real, o time de banco cria com BIGINT IDENTITY e o trigger
-- fn_touch_updated_at, como as outras tabelas.
CREATE TABLE IF NOT EXISTS venus.google_oauth_tokens (
    google_oauth_token_id SERIAL PRIMARY KEY,
    fk_user_id INT NOT NULL UNIQUE REFERENCES venus.users(user_id) ON DELETE CASCADE,
    encrypted_refresh_token BYTEA NOT NULL,
    scope TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
