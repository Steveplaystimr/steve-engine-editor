#include "box2d/box2d.h"

#include <cstddef>
#include <cstdint>
#include <cmath>
#include <memory>
#include <vector>


#if defined(_WIN32)
#define ENGINE_EXPORT extern "C" __declspec(dllexport)
#else
#define ENGINE_EXPORT extern "C" __attribute__((visibility("default")))
#endif

struct PhysicsBody {
    b2BodyId id;
    b2ShapeId shape;
};

struct PhysicsWorld {
    b2WorldId id;
    std::vector<std::unique_ptr<PhysicsBody>> bodies;
};

ENGINE_EXPORT int engine_cull_rectangles(
    const float* bounds,
    std::size_t count,
    float viewport_left,
    float viewport_top,
    float viewport_right,
    float viewport_bottom,
    std::uint8_t* visible
) noexcept {
    if (count > 0 && (bounds == nullptr || visible == nullptr)) {
        return 0;
    }

    for (std::size_t index = 0; index < count; ++index) {
        const float left = bounds[index * 4];
        const float top = bounds[index * 4 + 1];
        const float right = bounds[index * 4 + 2];
        const float bottom = bounds[index * 4 + 3];
        visible[index] = static_cast<std::uint8_t>(
            right >= viewport_left
            && bottom >= viewport_top
            && left <= viewport_right
            && top <= viewport_bottom
        );
    }

    return 1;
}

ENGINE_EXPORT void* engine_physics_world_create(float gravity_x, float gravity_y) noexcept {
    auto world = std::make_unique<PhysicsWorld>();
    b2WorldDef definition = b2DefaultWorldDef();
    definition.gravity = {gravity_x, gravity_y};
    world->id = b2CreateWorld(&definition);
    if (B2_IS_NULL(world->id)) {
        return nullptr;
    }
    return world.release();
}

ENGINE_EXPORT void engine_physics_world_destroy(void* handle) noexcept {
    auto* world = static_cast<PhysicsWorld*>(handle);
    if (world == nullptr) {
        return;
    }
    b2DestroyWorld(world->id);
    delete world;
}

ENGINE_EXPORT void engine_physics_world_set_gravity(
    void* handle, float gravity_x, float gravity_y
) noexcept {
    auto* world = static_cast<PhysicsWorld*>(handle);
    if (world != nullptr && b2World_IsValid(world->id)) {
        b2World_SetGravity(world->id, {gravity_x, gravity_y});
    }
}

ENGINE_EXPORT void engine_physics_world_step(
    void* handle, float time_step, int sub_steps
) noexcept {
    auto* world = static_cast<PhysicsWorld*>(handle);
    if (
        world != nullptr && b2World_IsValid(world->id)
        && time_step > 0.0f && sub_steps > 0
    ) {
        b2World_Step(world->id, time_step, sub_steps);
    }
}

ENGINE_EXPORT void* engine_physics_body_create(
    void* world_handle,
    int body_type,
    float x,
    float y,
    float angle,
    float half_width,
    float half_height,
    float density,
    float friction,
    float restitution
) noexcept {
    auto* world = static_cast<PhysicsWorld*>(world_handle);
    if (
        world == nullptr || !b2World_IsValid(world->id)
        || body_type < b2_staticBody || body_type > b2_dynamicBody
        || half_width <= 0.0f || half_height <= 0.0f
        || density < 0.0f || friction < 0.0f || friction > 1.0f
        || restitution < 0.0f || restitution > 1.0f
        || !std::isfinite(x) || !std::isfinite(y) || !std::isfinite(angle)
        || !std::isfinite(half_width) || !std::isfinite(half_height)
        || !std::isfinite(density) || !std::isfinite(friction)
        || !std::isfinite(restitution)
    ) {
        return nullptr;
    }

    b2BodyDef body_definition = b2DefaultBodyDef();
    body_definition.type = static_cast<b2BodyType>(body_type);
    body_definition.position = {x, y};
    body_definition.rotation = b2MakeRot(angle);
    b2BodyId body_id = b2CreateBody(world->id, &body_definition);
    if (B2_IS_NULL(body_id)) {
        return nullptr;
    }

    b2ShapeDef shape_definition = b2DefaultShapeDef();
    shape_definition.density = density;
    shape_definition.material.friction = friction;
    shape_definition.material.restitution = restitution;
    b2Polygon box = b2MakeBox(half_width, half_height);
    b2ShapeId shape_id = b2CreatePolygonShape(body_id, &shape_definition, &box);

    auto body = std::make_unique<PhysicsBody>();
    body->id = body_id;
    body->shape = shape_id;
    PhysicsBody* result = body.get();
    world->bodies.push_back(std::move(body));
    return result;
}

ENGINE_EXPORT int engine_physics_body_get_state(
    void* body_handle,
    float* x,
    float* y,
    float* angle,
    float* velocity_x,
    float* velocity_y
) noexcept {
    auto* body = static_cast<PhysicsBody*>(body_handle);
    if (
        body == nullptr || x == nullptr || y == nullptr || angle == nullptr
        || velocity_x == nullptr || velocity_y == nullptr
    ) {
        return 0;
    }
    b2WorldTransform transform = b2Body_GetTransform(body->id);
    b2Vec2 velocity = b2Body_GetLinearVelocity(body->id);
    *x = transform.p.x;
    *y = transform.p.y;
    *angle = b2Rot_GetAngle(transform.q);
    *velocity_x = velocity.x;
    *velocity_y = velocity.y;
    return 1;
}

ENGINE_EXPORT void engine_physics_body_destroy(
    void* world_handle, void* body_handle
) noexcept {
    auto* world = static_cast<PhysicsWorld*>(world_handle);
    auto* body = static_cast<PhysicsBody*>(body_handle);
    if (world == nullptr || body == nullptr || !b2World_IsValid(world->id)) {
        return;
    }
    b2DestroyBody(body->id);
    for (auto item = world->bodies.begin(); item != world->bodies.end(); ++item) {
        if (item->get() == body) {
            world->bodies.erase(item);
            return;
        }
    }
}

ENGINE_EXPORT void engine_physics_body_set_velocity(
    void* body_handle, float velocity_x, float velocity_y
) noexcept {
    auto* body = static_cast<PhysicsBody*>(body_handle);
    if (body != nullptr) {
        b2Body_SetLinearVelocity(body->id, {velocity_x, velocity_y});
    }
}

ENGINE_EXPORT void engine_physics_body_set_type(
    void* body_handle, int body_type
) noexcept {
    auto* body = static_cast<PhysicsBody*>(body_handle);
    if (
        body != nullptr
        && body_type >= b2_staticBody && body_type <= b2_dynamicBody
    ) {
        b2Body_SetType(body->id, static_cast<b2BodyType>(body_type));
    }
}

ENGINE_EXPORT void engine_physics_body_set_material(
    void* body_handle, float friction, float restitution
) noexcept {
    auto* body = static_cast<PhysicsBody*>(body_handle);
    if (
        body != nullptr && std::isfinite(friction)
        && std::isfinite(restitution) && friction >= 0.0f
        && friction <= 1.0f && restitution >= 0.0f && restitution <= 1.0f
    ) {
        b2Shape_SetFriction(body->shape, friction);
        b2Shape_SetRestitution(body->shape, restitution);
    }
}

ENGINE_EXPORT void engine_physics_body_apply_force(
    void* body_handle, float force_x, float force_y
) noexcept {
    auto* body = static_cast<PhysicsBody*>(body_handle);
    if (body != nullptr) {
        b2Body_ApplyForceToCenter(body->id, {force_x, force_y}, true);
    }
}

ENGINE_EXPORT void engine_physics_body_apply_impulse(
    void* body_handle, float impulse_x, float impulse_y
) noexcept {
    auto* body = static_cast<PhysicsBody*>(body_handle);
    if (body != nullptr) {
        b2Body_ApplyLinearImpulseToCenter(body->id, {impulse_x, impulse_y}, true);
    }
}
ENGINE_EXPORT uint64_t engine_physics_body_create_revolute_joint(
    void* world_handle,
    void* body_a_handle,
    void* body_b_handle,
    float anchor_x,
    float anchor_y
) noexcept {
    auto* world = static_cast<PhysicsWorld*>(world_handle);
    auto* body_a = static_cast<PhysicsBody*>(body_a_handle);
    auto* body_b = static_cast<PhysicsBody*>(body_b_handle);
    if (
        world == nullptr || !b2World_IsValid(world->id)
        || body_a == nullptr || body_b == nullptr
        || !std::isfinite(anchor_x) || !std::isfinite(anchor_y)
    ) {
        return 0;
    }

    b2RevoluteJointDef definition = b2DefaultRevoluteJointDef();
    definition.base.bodyIdA = body_a->id;
    definition.base.bodyIdB = body_b->id;
    definition.base.localFrameA.p = b2Body_GetLocalPoint(
        body_a->id, {anchor_x, anchor_y}
    );
    definition.base.localFrameB.p = b2Body_GetLocalPoint(
        body_b->id, {anchor_x, anchor_y}
    );
    b2JointId joint = b2CreateRevoluteJoint(world->id, &definition);
    return B2_IS_NULL(joint) ? 0 : b2StoreJointId(joint);
}
