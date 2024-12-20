import pygame
import random

# Initialize Pygame
pygame.init()

# Screen settings
SCREEN_WIDTH, SCREEN_HEIGHT = 800, 600
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
pygame.display.set_caption("Beastborn Tactics")
clock = pygame.time.Clock()

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (200, 50, 50)
GREEN = (50, 200, 50)

# Game settings
TILE_SIZE = 100
PLAYER_SPEED = 5

# Classes
class Beast(pygame.sprite.Sprite):
    def __init__(self, name, hp, attack, color, x, y):
        super().__init__()
        self.name = name
        self.hp = hp
        self.attack = attack
        self.image = pygame.Surface((TILE_SIZE - 10, TILE_SIZE - 10))
        self.image.fill(color)
        self.rect = self.image.get_rect()
        self.rect.topleft = (x, y)

    def draw_health_bar(self, surface):
        pygame.draw.rect(surface, RED, (self.rect.x, self.rect.y - 10, TILE_SIZE - 10, 5))
        pygame.draw.rect(
            surface, GREEN, (self.rect.x, self.rect.y - 10, (TILE_SIZE - 10) * (self.hp / 100), 5)
        )

class Player(Beast):
    def move(self, direction):
        if direction == "left":
            self.rect.x -= PLAYER_SPEED
        elif direction == "right":
            self.rect.x += PLAYER_SPEED
        elif direction == "up":
            self.rect.y -= PLAYER_SPEED
        elif direction == "down":
            self.rect.y += PLAYER_SPEED

# Create Beasts
beasts = pygame.sprite.Group()
player = Player("Hero", 100, 20, BLUE := (50, 50, 200), SCREEN_WIDTH // 2, SCREEN_HEIGHT - TILE_SIZE)
beasts.add(player)

# Enemy beasts
for i in range(5):
    enemy = Beast(
        f"Beast {i + 1}",
        random.randint(50, 100),
        random.randint(10, 30),
        (random.randint(100, 255), random.randint(100, 255), random.randint(100, 255)),
        random.randint(0, SCREEN_WIDTH - TILE_SIZE),
        random.randint(0, SCREEN_HEIGHT // 2),
    )
    beasts.add(enemy)

def main():
    running = True
    while running:
        screen.fill(WHITE)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        # Player Movement
        keys = pygame.key.get_pressed()
        if keys[pygame.K_LEFT]:
            player.move("left")
        if keys[pygame.K_RIGHT]:
            player.move("right")
        if keys[pygame.K_UP]:
            player.move("up")
        if keys[pygame.K_DOWN]:
            player.move("down")

        # Draw all beasts
        for beast in beasts:
            screen.blit(beast.image, beast.rect)
            beast.draw_health_bar(screen)

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()

if __name__ == "__main__":
    main()
