# Matik - React with Typescript vs Javascript

Date: 2025-07-28

Status: `Accepted`

Collaborators: @julie-trias

## Context
Building Matik with a Go (Golang) backend, the choice between JavaScript and TypeScript for the frontend development depends on Matik's specific needs.

### Option I - Javascript
#### Pros
- Easier Learning Curve
    - Especially for simpler projects or teams with limited TypeScript experience, JavaScript is generally easier to pick up initially.
- Larger Community
    - JavaScript has a vast and diverse community with a huge range of tools and libraries, making it easy to find resources and support.
- Rapid Prototyping
    - Well-suited for quick development and iterative prototyping due to its dynamic and less strict nature

#### Cons
- Potential for Errors
    - Loosely typed nature can make it prone to type-related errors, especially in large and complex projects
- Less Maintainable for Large Projects
    - Without strong typing, it can be harder to manage and refactor large codebases, potentially leading to bugs and decreased productivity

### Option II - Typescript
#### Pros
- Enhanced Type Safety
    - Being a superset of JavaScript, TypeScript adds optional static typing, enabling type checking at compile time and helping catch errors early
- Improved Maintainability for Large Projects
    - Static typing and features like interfaces and classes make large-scale applications easier to organize, understand, and maintain
- Better Tooling and IDE Support
    - Static typing provides better autocompletion and error detection within development environments, boosting developer productivity
- Better Code Structuring
    - Offers improved object-oriented programming features and code organization techniques compared to JavaScript

#### Cons
- Steeper Learning Curve
    - Learning TypeScript requires understanding its type system and features beyond standard JavaScript
- Smaller Community
    - While growing, its community is smaller than JavaScript's, potentially making it slightly harder to find niche solutions or support

## Decision
Typescript as it is better suited for more complex and large projects such as Matik.

## Considerations
The cons of Javascript simply outweigh the cost.

## Consequences
Typescript will make us more future-proof as more and more projects migrate to Typescript from Javascript.
